"""网页托管版（hosted.py）测试：登录门、会话、SESSDATA 加密、模板 CRUD。

登录链路把 httpx.post mock 成直通授权服务器的测试客户端，不发真实请求。
"""

import re
import sqlite3

import pytest

import hosted
from app import create_app as _lic_app
from hosted import create_app as _hosted_app


class _Resp:
    """把授权服务器测试客户端的响应包成 hosted.login 期望的 httpx 形状。"""

    def __init__(self, r):
        self.status_code = r.status_code
        self._d = r.get_json(silent=True)
        self.content = b"1" if self._d is not None else b""

    def json(self):
        return self._d


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = str(tmp_path / "lic.db")
    lic = _lic_app(db_path=db, server_secret="s", admin_key="k", glm_api_key="g")
    lc = lic.test_client()
    lc.post("/admin/generate", data={"key": "k", "count": "1", "note": "web"})
    code = re.search(r"BP-[0-9A-F]{4}(?:-[0-9A-F]{4}){3}",
                     lc.get("/admin?key=k").get_data(as_text=True)).group(0)

    host = _hosted_app(db_path=db, server_secret="s", license_server_url="http://x")
    hc = host.test_client()

    def fake_post(url, json=None, timeout=None):
        assert url.endswith("/api/web/login")
        return _Resp(lc.post("/api/web/login", json=json))

    monkeypatch.setattr(hosted.httpx, "post", fake_post)
    return {"lc": lc, "hc": hc, "code": code, "db": db}


def test_gate_and_login_flow(env):
    hc, code = env["hc"], env["code"]
    # 未登录：页面能开，API 全部 401（前端据此弹登录浮层）
    assert hc.get("/").status_code == 200
    assert hc.get("/api/status").status_code == 401
    # 错码 403
    assert hc.post("/api/login", json={"code": "BP-BAD"}).status_code == 403
    # 正确码 → 登录成功
    r = hc.post("/api/login", json={"code": code})
    assert r.status_code == 200 and r.get_json()["usage"]["daily_quota"] == 50
    d = hc.get("/api/status").get_json()
    assert d["hosted"] and d["sessdata_configured"] is False
    # 登出后回到 401
    hc.post("/api/logout")
    assert hc.get("/api/status").status_code == 401


def test_sessdata_encrypted_at_rest(env):
    hc, db, code = env["hc"], env["db"], env["code"]
    hc.post("/api/login", json={"code": code})
    hc.post("/api/config/save", json={"sessdata": "SECRET-SESSDATA-XYZ"})
    assert hc.get("/api/status").get_json()["sessdata_configured"] is True
    # 落库的是密文，不含明文
    raw = sqlite3.connect(db).execute("SELECT sessdata_enc FROM user_secrets").fetchone()[0]
    assert "SECRET-SESSDATA-XYZ" not in raw
    # 存空 = 清除
    hc.post("/api/config/save", json={"sessdata": ""})
    assert hc.get("/api/status").get_json()["sessdata_configured"] is False


def test_prompts_crud(env):
    hc, code = env["hc"], env["code"]
    hc.post("/api/login", json={"code": code})
    pid = hc.post("/api/prompts", json={"name": "抽梗", "prompt": "列出梗"}).get_json()["id"]
    assert hc.get("/api/prompts").get_json()["prompts"][0]["name"] == "抽梗"
    hc.post("/api/prompts", json={"id": pid, "name": "改", "prompt": "p2"})
    assert hc.get("/api/prompts").get_json()["prompts"][0]["name"] == "改"
    assert hc.delete(f"/api/prompts/{pid}").status_code == 200
    assert hc.delete(f"/api/prompts/{pid}").status_code == 404


def test_disabled_code_kicks_session(env):
    lc, hc, code = env["lc"], env["hc"], env["code"]
    hc.post("/api/login", json={"code": code})
    assert hc.get("/api/status").status_code == 200
    # 卖家在管理后台禁用该码 → 网页会话立即失效
    html = lc.get("/admin?key=k").get_data(as_text=True)
    lid = re.search(r'name="id" value="(\d+)"', html).group(1)
    lc.post("/admin/action", data={"key": "k", "id": lid, "op": "toggle"})
    assert hc.get("/api/status").status_code == 403


def test_web_session_limit(env):
    """一码最多 2 个网页会话，第 3 个登录挤掉最早那个（防共享）。"""
    app = env["hc"].application
    code = env["code"]
    c1, c2 = app.test_client(), app.test_client()
    assert c1.post("/api/login", json={"code": code}).status_code == 200
    assert c2.post("/api/login", json={"code": code}).status_code == 200
    assert c1.get("/api/status").status_code == 200
    assert c2.get("/api/status").status_code == 200
    # 第 3 个会话登录 → 挤掉最早（c1），c2 仍有效
    c3 = app.test_client()
    assert c3.post("/api/login", json={"code": code}).status_code == 200
    assert c3.get("/api/status").status_code == 200
    assert c1.get("/api/status").status_code == 401
    assert c2.get("/api/status").status_code == 200


def test_api_key_config_and_encryption(env):
    hc, db, code = env["hc"], env["db"], env["code"]
    hc.post("/api/login", json={"code": code})
    hc.post("/api/config/save", json={"provider": "deepseek", "api_key": "sk-test-123"})
    d = hc.get("/api/config/get").get_json()
    assert d["provider"] == "deepseek" and d["api_key_configured"] is True
    # 落库是密文，不含明文 key
    raw = sqlite3.connect(db).execute("SELECT api_key_enc, provider FROM user_secrets").fetchone()
    assert raw[1] == "deepseek" and "sk-test-123" not in raw[0]
    # 未知提供商拒绝
    assert hc.post("/api/config/save", json={"provider": "openai"}).status_code == 400


def test_summarize_requires_api_key(env):
    hc, code = env["hc"], env["code"]
    hc.post("/api/login", json={"code": code})
    r = hc.post("/api/summarize", json={"url": "https://www.bilibili.com/video/BV1xx", "mode": "meta"})
    assert r.status_code == 400 and "API Key" in r.get_json()["error"]
