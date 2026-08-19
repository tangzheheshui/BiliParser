"""Web 工作台冒烟测试：起真实服务器（随机端口），只测离线路径。"""

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from biliparser import config, web


@pytest.fixture()
def server(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    srv = web.make_server(config.load_config(), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


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
