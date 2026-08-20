"""授权服务器 API 测试：激活/验证/解绑/禁用/配额/转发全流程。"""

import json

import pytest

from app import create_app, issue_token, parse_token


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = create_app(
        db_path=str(tmp_path / "test.db"),
        server_secret="test-secret",
        admin_key="admin-key",
        glm_api_key="glm-key",
    )
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _gen(client, count=1, **kw):
    form = {"key": "admin-key", "count": str(count), **kw}
    r = client.post("/admin/generate", data=form)
    assert r.status_code == 200
    return r


def _first_code(client) -> str:
    # 从管理页 HTML 里抠第一个激活码（测试够用）
    import re
    html = client.get("/admin?key=admin-key").get_data(as_text=True)
    m = re.search(r"BP-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}", html)
    return m.group(0)


def _activate(client, code, fp="MAC-1"):
    return client.post("/api/activate", json={"code": code, "fingerprint": fp})


# ---------- token 原语 ----------

def test_token_roundtrip():
    t = issue_token("s", 7, "FP", 1900000000)
    assert parse_token("s", t) == (7, "FP", 1900000000)
    assert parse_token("wrong-secret", t) is None
    assert parse_token("s", t[:-2] + "xx") is None


# ---------- 激活 ----------

def test_activate_unknown_code(client):
    r = _activate(client, "BP-XXXX")
    assert r.status_code == 403 and "无效" in r.get_json()["error"]


def test_activate_bind_and_idempotent(client):
    _gen(client)
    code = _first_code(client)
    r = _activate(client, code, "MAC-1")
    assert r.status_code == 200 and r.get_json()["token"]
    # 同设备重复激活 → 幂等，同样成功
    r2 = _activate(client, code, "MAC-1")
    assert r2.status_code == 200


def test_activate_second_device_rejected(client):
    _gen(client)
    code = _first_code(client)
    assert _activate(client, code, "MAC-1").status_code == 200
    r = _activate(client, code, "MAC-2")
    assert r.status_code == 403 and "其他设备" in r.get_json()["error"]


def test_expired_code_rejected(client):
    _gen(client, days="1")
    code = _first_code(client)
    import sqlite3
    conn = sqlite3.connect("test.db")
    conn.execute("UPDATE licenses SET expires_at='2000-01-01T00:00:00'")
    conn.commit()
    conn.close()
    r = _activate(client, code)
    assert r.status_code == 403 and "过期" in r.get_json()["error"]


# ---------- 验证 ----------

def _token_of(client, code) -> str:
    return _activate(client, code).get_json()["token"]


def test_verify_ok_and_revocation(client):
    _gen(client)
    token = _token_of(client, _first_code(client))
    r = client.post("/api/verify", json={"token": token})
    d = r.get_json()
    assert d["valid"] and d["valid_until"] and d["usage"]["daily_quota"] > 0

    # 管理端禁用 → 立即失效（远程吊销）
    import re
    html = client.get("/admin?key=admin-key").get_data(as_text=True)
    lid = re.search(r'name="id" value="(\d+)"', html).group(1)
    client.post("/admin/action", data={"key": "admin-key", "id": lid, "op": "toggle"})
    d = client.post("/api/verify", json={"token": token}).get_json()
    assert not d["valid"]


def test_verify_garbage(client):
    d = client.post("/api/verify", json={"token": "junk"}).get_json()
    assert not d["valid"]


def test_unbind_allows_new_device(client):
    _gen(client)
    code = _first_code(client)
    assert _activate(client, code, "MAC-1").status_code == 200
    import re
    html = client.get("/admin?key=admin-key").get_data(as_text=True)
    lid = re.search(r'name="id" value="(\d+)"', html).group(1)
    client.post("/admin/action", data={"key": "admin-key", "id": lid, "op": "unbind"})
    # 旧 token 因指纹不匹配失效，新设备可激活
    r = _activate(client, code, "MAC-2")
    assert r.status_code == 200


# ---------- AI 代理 ----------

def _auth(client, token):
    return {"Authorization": f"Bearer {token}"}


def test_ai_chat_forwards_and_counts(client, monkeypatch):
    import app as app_mod
    captured = {}

    class _Resp:
        status_code = 200
        text = json.dumps({"choices": [{"message": {"content": "ok"}}]})

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json, headers=headers)
        return _Resp()

    monkeypatch.setattr(app_mod.httpx, "post", fake_post)
    _gen(client)
    token = _token_of(client, _first_code(client))
    body = {"model": "客户端想用贵的", "messages": [{"role": "user", "content": "hi"}]}
    r = client.post("/api/ai/chat", json=body, headers=_auth(client, token))
    assert r.status_code == 200
    # 模型被服务器强制覆盖
    assert captured["json"]["model"] == "glm-4-flash"
    assert captured["headers"]["Authorization"] == "Bearer glm-key"
    # 用量 +1
    q = client.get("/api/quota", headers=_auth(client, token)).get_json()
    assert q["today_used"] == 1


def test_ai_chat_quota_exceeded(client, monkeypatch):
    import sqlite3
    _gen(client)
    code = _first_code(client)
    token = _token_of(client, code)
    conn = sqlite3.connect("test.db")
    import datetime
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    conn.execute(
        "INSERT INTO usage (license_id, day, count) "
        "SELECT id, ?, daily_quota FROM licenses WHERE code=?", (today, code)
    )
    conn.commit()
    conn.close()
    r = client.post("/api/ai/chat", json={"messages": [{"role": "user", "content": "hi"}]},
                    headers=_auth(client, token))
    assert r.status_code == 429 and "上限" in r.get_json()["error"]


def test_ai_chat_bad_token(client):
    r = client.post("/api/ai/chat", json={"messages": [{"role": "user", "content": "hi"}]},
                    headers=_auth(client, "junk"))
    assert r.status_code == 403


def test_admin_requires_key(client):
    assert client.get("/admin").status_code == 403
    assert client.get("/admin?key=wrong").status_code == 403
    assert client.get("/admin?key=admin-key").status_code == 200


# ---------- 网页版登录（一码通用，不占设备位） ----------

def _web_login(client, code):
    return client.post("/api/web/login", json={"code": code})


def test_web_login_ok_and_quota_shared(client, monkeypatch):
    import app as app_mod
    _gen(client)
    code = _first_code(client)
    # 桌面版先激活，占住设备位
    assert _activate(client, code, "MAC-1").status_code == 200
    # 同一个码网页登录 → 成功（一码通用）
    r = _web_login(client, code)
    assert r.status_code == 200
    d = r.get_json()
    assert d["license_id"] and parse_token("test-secret", d["token"])[1] == "WEB"
    # WEB token 可验证
    assert client.post("/api/verify", json={"token": d["token"]}).get_json()["valid"]
    # WEB token 走 AI 代理并计入同一份配额（与桌面版共享）
    class _Resp:
        status_code = 200
        text = json.dumps({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(app_mod.httpx, "post", lambda *a, **k: _Resp())
    r = client.post("/api/ai/chat", json={"messages": [{"role": "user", "content": "hi"}]},
                    headers=_auth(client, d["token"]))
    assert r.status_code == 200
    q = client.get("/api/quota", headers=_auth(client, d["token"])).get_json()
    assert q["today_used"] == 1


def test_web_login_bad_code(client):
    assert _web_login(client, "BP-NOPE").status_code == 403


def test_admin_export_only_unactivated(client):
    """导出只含未激活码，已绑设备的码不导出（防把卖过的码再卖给新买家）。"""
    _gen(client, count=3)
    _activate(client, _first_code(client), "MAC-1")  # 激活 1 个
    r = client.get("/admin/export?key=admin-key")
    assert r.status_code == 200 and r.mimetype.startswith("text/plain")
    codes = r.get_data(as_text=True).strip().split("\n")
    assert len(codes) == 2 and all(c.startswith("BP-") for c in codes)
    assert client.get("/admin/export").status_code == 403  # 无 key 拒绝


# ---------- 官网与安装包分发 ----------

def test_site_index_and_download(client, tmp_path):
    downloads = tmp_path / "dl"
    downloads.mkdir()
    (downloads / "BiliParser-macOS.dmg").write_bytes(b"fake-dmg")
    (downloads / "version.json").write_text('{"version":"v9"}')
    client.application.config["DOWNLOADS_DIR"] = str(downloads)

    r = client.get("/")
    assert r.status_code == 200 and "BiliParser" in r.get_data(as_text=True)
    r = client.get("/download/BiliParser-macOS.dmg")
    assert r.status_code == 200 and r.data == b"fake-dmg"
    assert r.headers["Content-Disposition"].startswith("attachment")
    assert client.get("/download/version.json").status_code == 200
    assert client.get("/download/nope.exe").status_code == 404
    assert client.get("/download/../app.py").status_code in (404, 403)  # 防穿越
