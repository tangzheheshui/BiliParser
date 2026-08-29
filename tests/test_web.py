"""Web 工作台冒烟测试：起真实服务器（随机端口），只测离线路径。"""

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from biliparser import config, licensing, web


@pytest.fixture()
def server(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    web._VALID_CACHE.clear()   # 校验缓存按值命中，跨用例必须清
    srv = web.make_server(config.load_config(), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    web._VALID_CACHE.clear()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.read()


def _post(url, obj):
    req = urllib.request.Request(
        url, data=json.dumps(obj).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_index_served(server):
    status, body = _get(server + "/")
    assert status == 200
    html = body.decode("utf-8")
    assert "BiliParser 工作台" in html


def test_status(server):
    status, body = _get(server + "/api/status")
    assert status == 200
    d = json.loads(body)
    assert d["endpoint"] in ("openai", "anthropic")
    assert "model" in d


def test_parse_bad_url_returns_clean_error(server):
    status, d = _post(server + "/api/parse", {"url": "不是链接"})
    assert status == 400
    assert d["error"]


def test_static_file_packaged():
    assert (Path(web.__file__).parent / "static" / "index.html").exists()


# ---------- 自定义模板 CRUD ----------

def test_prompts_crud_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "PROMPTS_PATH", tmp_path / "prompts.json")
    assert web.load_prompts() == []
    created = web.upsert_prompt({"name": "三句话版", "prompt": "用三句话总结视频"})
    assert web.load_prompts()[0]["name"] == "三句话版"
    # 更新（同 id）
    web.upsert_prompt({"id": created["id"], "name": "两句话版", "prompt": "两句话"})
    got = web.load_prompts()
    assert len(got) == 1 and got[0]["name"] == "两句话版"
    # 非法输入
    with pytest.raises(web.ApiError):
        web.upsert_prompt({"name": "", "prompt": "x"})
    # 删除
    assert web.delete_prompt(created["id"])["deleted"] == created["id"]
    assert web.load_prompts() == []
    with pytest.raises(web.ApiError):
        web.delete_prompt(created["id"])


def test_prompts_api_crud(server, monkeypatch, tmp_path):
    monkeypatch.setattr(web, "PROMPTS_PATH", tmp_path / "prompts.json")
    status, d = _post(server + "/api/prompts", {"name": "微博体", "prompt": "写成微博"})
    assert status == 200 and d["id"]
    status, lst = _get(server + "/api/prompts")
    assert status == 200 and len(json.loads(lst)["prompts"]) == 1
    # 用不存在的模板总结 → 404 干净报错
    status, d = _post(server + "/api/summarize", {"url": "BV1cXgp6aESY", "mode": "custom", "prompt_id": "nope"})
    assert status == 404 and d["error"]


def test_summarize_custom_uses_user_prompt(monkeypatch):
    from biliparser import summarizer
    captured = {}
    monkeypatch.setattr(summarizer, "_chat", lambda cfg, msgs: captured.update(p=msgs[0]["content"]) or "ok")
    summarizer.summarize_custom("[00:01] x", "t", object(), "用户自定义提示词")
    assert captured["p"] == "用户自定义提示词"


# ---------- 配置真实值回显 + 保存校验（8/25 拍板） ----------

def _no_validate(monkeypatch):
    monkeypatch.setattr(web, "_validate_sessdata", lambda sd, _force=False: None)
    monkeypatch.setattr(web, "_validate_api_key", lambda cfg, _force=False: None)


def test_config_get_returns_real_values(server):
    """面板回填：config/get 返回真实值 + 缩略（不再只有「已配置」布尔）。"""
    # 服务器启动时绑定 cfg 对象，这里直接改它（等价于配置已存在）
    cfg = web.Handler.cfg
    cfg.sessdata = "sd-abcdefghijklmnop"
    cfg.glm_api_key = "sk-1234567890abcdef"
    status, body = _get(server + "/api/config/get")
    d = json.loads(body)
    assert status == 200
    assert d["sessdata_value"] == "sd-abcdefghijklmnop"
    assert d["api_key_value"] == "sk-1234567890abcdef"
    assert d["sessdata_short"] == "sd-a…mnop"
    assert d["api_key_short"] == "sk-1…cdef"
    assert d["sessdata_configured"] and d["glm_key_configured"]


def test_status_shows_shorts(server):
    cfg = web.Handler.cfg
    cfg.sessdata = "sd-abcdefghijklmnop"
    cfg.glm_api_key = "sk-1234567890abcdef"
    status, body = _get(server + "/api/status")
    d = json.loads(body)
    assert d["sessdata_short"] == "sd-a…mnop"
    assert d["api_key_short"] == "sk-1…cdef"


def test_config_save_clears_on_empty(server, monkeypatch):
    """清空=删除：提交空串即删掉该配置（不再是「留空保持不变」）。"""
    _no_validate(monkeypatch)
    # 环境变量会覆盖文件值（config.py 设计），测试里先摘掉才是「清空后的真实状态」
    for var in ("ZHIPUAI_API_KEY", "GLM_API_KEY", "BILI_SESSDATA",
                "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    config.update_config({"sessdata": "sd-abcdefghijklmnop",
                          "glm.api_key": "sk-1234567890abcdef"})
    status, d = _post(server + "/api/config/save", {"sessdata": "", "api_key": ""})
    assert status == 200
    assert d["sessdata_configured"] is False
    assert d["glm_key_configured"] is False
    assert config.load_config(require=()).sessdata == ""


def test_config_save_validates_and_reports(server, monkeypatch):
    """保存后实测并返回 *_valid：失效 SESSDATA → false，Key 有效 → true。"""
    monkeypatch.setattr(web, "_validate_sessdata", lambda sd, _force=False: False)
    monkeypatch.setattr(web, "_validate_api_key", lambda cfg, _force=False: True)
    status, d = _post(server + "/api/config/save",
                      {"sessdata": "expired-one", "api_key": "sk-good"})
    assert status == 200
    assert d["sessdata_valid"] is False and d["api_key_valid"] is True
    assert d["sessdata_value"] == "expired-one"


def test_config_save_provider_switch(server, monkeypatch):
    _no_validate(monkeypatch)
    status, d = _post(server + "/api/config/save",
                      {"provider": "deepseek", "api_key": "sk-ds"})
    assert status == 200 and d["provider"] == "deepseek"
    fresh = config.load_config(require=())
    assert fresh.glm_provider == "deepseek"
    assert fresh.glm_api_key == "sk-ds"
    assert "deepseek" in fresh.glm_base_url


# ---------- Key 自动识别提供商 / 关于官网 / 静态文件 ----------

def test_config_save_auto_detects_provider(server, monkeypatch):
    """免选提供商：粘贴 Key 自动识别（sk- → DeepSeek；id.secret → 智谱）。"""
    _no_validate(monkeypatch)
    for var in ("ZHIPUAI_API_KEY", "GLM_API_KEY",
                "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    status, d = _post(server + "/api/config/save", {"api_key": "sk-abc123"})
    assert status == 200 and d["provider"] == "deepseek"
    status, d = _post(server + "/api/config/save",
                      {"api_key": "0123456789abcdef.0123456789abcdef"})
    assert status == 200 and d["provider"] == "zhipu"


def test_open_official_opens_system_browser(server, monkeypatch):
    """「关于」里的官网链接：走本机服务调系统浏览器（pywebview 里 _blank 无效）。"""
    import webbrowser
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    status, body = _get(server + "/open-official")
    assert status == 200 and "官网" in body.decode("utf-8")
    assert opened == [licensing.OFFICIAL_SITE]


def test_static_file_served(server):
    from pathlib import Path
    png = Path(web.__file__).parent / "static" / "sessdata-guide.png"
    if png.exists():   # 教程截图在则应能取到；不在也不 500
        status, body = _get(server + "/static/sessdata-guide.png")
        assert status == 200 and body[:4] == b"\x89PNG"
    try:
        status, _ = _get(server + "/static/nope.png")
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 404


# ---------- 状态卡真实校验（/api/config/validate + 按值缓存） ----------

def test_config_validate_endpoint(server, monkeypatch):
    """状态卡异步校验端点：返回两项 *_valid；未配置项 = None 不武断判死。"""
    from biliparser import bilibili
    monkeypatch.setattr(bilibili, "is_logged_in", lambda c: True)
    cfg = web.Handler.cfg
    cfg.sessdata = "sd-validate-test"
    cfg.glm_api_key = ""
    status, body = _get(server + "/api/config/validate")
    d = json.loads(body)
    assert status == 200 and d["sessdata_valid"] is True and d["api_key_valid"] is None


def test_validate_cached_per_value(server, monkeypatch):
    """同值不重复联网：两次校验只打一次真实接口；值变了才重新测。"""
    from biliparser import bilibili
    calls = []
    monkeypatch.setattr(bilibili, "is_logged_in", lambda c: calls.append(1) or True)
    cfg = web.Handler.cfg
    cfg.sessdata = "sd-cache-hit"
    _get(server + "/api/config/validate")
    _get(server + "/api/config/validate")
    assert len(calls) == 1
    cfg.sessdata = "sd-cache-changed"
    _get(server + "/api/config/validate")
    assert len(calls) == 2


def test_config_get_peeks_cached_validity(server, monkeypatch):
    """打开面板不主动联网，但启动校验已测过的结果要透出（红/绿真实状态）。"""
    from biliparser import bilibili
    monkeypatch.setattr(bilibili, "is_logged_in", lambda c: False)   # SESSDATA 失效
    cfg = web.Handler.cfg
    cfg.sessdata = "sd-expired-one"
    cfg.glm_api_key = ""   # 环境变量可能填过 Key 槽，这里显式未配置
    status, _ = _get(server + "/api/config/validate")   # 启动时的异步校验，暖缓存
    assert status == 200
    status, body = _get(server + "/api/config/get")
    d = json.loads(body)
    assert status == 200 and d["sessdata_valid"] is False
    # 值没测过（如刚换的 Key）→ None，面板显示中性提示而不是瞎猜
    assert d["api_key_valid"] is None


def test_validate_api_key_flags_insufficient_balance(server, monkeypatch):
    """余额不足的 Key 也是「已配置但不生效」→ False（红色未生效），不是 None。"""
    from biliparser import summarizer
    def boom(cfg, msgs):
        raise summarizer.SummarizeError("AI 调用失败：余额不足，请充值后重试")
    monkeypatch.setattr(summarizer, "_chat", boom)
    cfg = web.Handler.cfg
    cfg.glm_api_key = "sk-broke-key"
    status, body = _get(server + "/api/config/validate")
    d = json.loads(body)
    assert status == 200 and d["api_key_valid"] is False


# ---------- 发行版激活门：不激活不能用 ----------

def test_gate_blocks_business_api_when_unactivated(server, monkeypatch):
    """未激活 → 业务 POST 全 403；只有 /api/license/* 放行。"""
    monkeypatch.setattr(licensing, "verify_local",
                        lambda: {"ok": False, "reason": "未激活"})
    cfg = web.Handler.cfg
    cfg.managed_server = "http://193.112.26.217:7900"
    for path, body in [("/api/parse", {"url": "BV1cXgp6aESY"}),
                       ("/api/subtitle", {"url": "BV1cXgp6aESY"}),
                       ("/api/summarize", {"url": "BV1cXgp6aESY"})]:
        status, d = _post(server + path, body)
        assert status == 403 and "未激活" in d["error"], (path, status, d)
    status, d = _post(server + "/api/license/state", {})
    assert status == 200 and d["activated"] is False


def test_gate_passes_when_activated(server, monkeypatch):
    """已激活（本地验签过）→ 业务接口放行（正常业务错误而非 403）。"""
    monkeypatch.setattr(licensing, "verify_local", lambda: {"ok": True})
    cfg = web.Handler.cfg
    cfg.managed_server = "http://193.112.26.217:7900"
    status, d = _post(server + "/api/parse", {"url": "不是链接"})
    assert status == 400          # 到达业务层（参数校验），不是门禁 403


def test_gate_off_for_direct_build(server):
    """直连自用版（未烧服务器地址）→ 不设门。"""
    cfg = web.Handler.cfg
    cfg.managed_server = ""
    status, d = _post(server + "/api/parse", {"url": "不是链接"})
    assert status == 400


# ---------- 启动联网核验（每次启动；后台解绑后老设备失效） ----------

def test_startup_verify_revoked_kicks_device(monkeypatch, tmp_path):
    """后台解绑 → 启动核验判 3 → 清凭证：老设备下次启动被踢回激活页。"""
    monkeypatch.setattr(licensing, "verify_local", lambda: {"ok": True})
    monkeypatch.setattr(licensing, "LICENSE_PATH", tmp_path / "lic.json")
    licensing._save("ABCD-1234-EFGH-5678", "AABBCCDDEEFF", "t", "x" * 64)
    calls = {}

    def fake_remote(url):
        calls["url"] = url
        licensing.clear_credential()               # 真实 verify_remote 吊销时清凭证
        return {"checked": True, "ok": False, "reason": "该激活码已在其他设备使用"}

    monkeypatch.setattr(licensing, "verify_remote", fake_remote)

    class _Cfg:
        managed_server = "http://tangzheheshui.cn/biliparser"

    web._startup_verify(_Cfg())
    assert calls["url"].endswith("/biliparser")
    assert not (tmp_path / "lic.json").exists()     # 凭证被清 → 激活门拦下


def test_startup_verify_offline_tolerant_and_direct_skipped(monkeypatch):
    """服务器不可达 → 放行；直连自用版（无服务器）→ 压根不联网。"""
    monkeypatch.setattr(licensing, "verify_local", lambda: {"ok": True})

    def no_net(_url):
        raise AssertionError("直连版不应联网核验")

    monkeypatch.setattr(licensing, "verify_remote", no_net)

    class _Direct:
        managed_server = ""

    web._startup_verify(_Direct())                 # 不抛 = 通过

    monkeypatch.setattr(licensing, "verify_remote",
                        lambda url: {"checked": False, "ok": True,
                                     "reason": "服务器不可达，离线放行"})

    class _Managed:
        managed_server = "http://s"

    web._startup_verify(_Managed())                # 不抛 = 通过


# ---------- 烧入文件读取（frozen 布局漂移回归）+ 更新提示 ----------

def test_bundled_text_finds_mirror_layout(monkeypatch, tmp_path):
    """烧入文件不在包目录、在 .app 的 Resources 镜像位置也读得到。

    2026-08-25 翻车回归：PyInstaller 布局漂移导致运行时 __file__ 与烧入
    文件不在一处，正式版被当成直连版显示「免激活」。
    """
    pkg = tmp_path / "Contents" / "Frameworks" / "biliparser"
    pkg.mkdir(parents=True)
    monkeypatch.setattr(licensing, "__file__", str(pkg / "licensing.py"))
    mirror = tmp_path / "Contents" / "Resources" / "biliparser"
    mirror.mkdir(parents=True)
    (mirror / "_dist_server.txt").write_text("http://mirror:7900\n")
    assert licensing.bundled_text("_dist_server.txt") == "http://mirror:7900"
    assert licensing.bundled_text("_sign_key.txt") == ""      # 不存在 → 空串


def test_update_check_endpoint(server, monkeypatch):
    """启动更新提示：官网版本更新 → true；同版本 → false；连不上 → 静默 false。"""
    class _R:
        def __init__(self, d): self._d = d
        def json(self): return self._d
    monkeypatch.setattr(web.httpx, "get", lambda url, timeout: _R({"version": "9.9.9"}))
    d = json.loads(_get(server + "/api/update-check")[1])
    assert d["update_available"] is True and d["latest"] == "9.9.9"
    monkeypatch.setattr(web.httpx, "get", lambda url, timeout: _R({"version": d["current"]}))
    d = json.loads(_get(server + "/api/update-check")[1])
    assert d["update_available"] is False
    def boom(url, timeout): raise OSError("网络不通")
    monkeypatch.setattr(web.httpx, "get", boom)
    d = json.loads(_get(server + "/api/update-check")[1])
    assert d["update_available"] is False and d["latest"] == ""
