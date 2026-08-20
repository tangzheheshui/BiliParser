"""summarizer 错误分支测试（mock httpx.post，不发真实请求）。"""

import pytest

from biliparser import summarizer


class _Resp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Cfg:
    glm_base_url = "https://example.invalid/v4"
    glm_api_key = "k"
    glm_model = "test"


def test_balance_error_not_retried(monkeypatch):
    """余额不足（1113 借 HTTP 429 返回）应立即报「请充值」，不重试。"""
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        return _Resp(
            429,
            {"error": {"code": "1113", "message": "余额不足或无可用资源包,请充值。"}},
        )

    monkeypatch.setattr(summarizer.httpx, "post", fake_post)
    with pytest.raises(summarizer.SummarizeError) as ei:
        summarizer._chat(_Cfg(), [{"role": "user", "content": "hi"}])
    assert "充值" in str(ei.value.hint)
    assert len(calls) == 1  # 没有白费第二次调用


def test_rate_limit_retried_once(monkeypatch):
    """真限流重试一次后仍失败，报限流错误。"""
    states = {"n": 0}

    def fake_post(url, **kw):
        states["n"] += 1
        return _Resp(429, {"error": {"message": "Requests rate limit exceeded"}})

    monkeypatch.setattr(summarizer.httpx, "post", fake_post)
    monkeypatch.setattr(summarizer.time, "sleep", lambda s: None)
    with pytest.raises(summarizer.SummarizeError) as ei:
        summarizer._chat(_Cfg(), [{"role": "user", "content": "hi"}])
    assert "429" in str(ei.value)
    assert states["n"] == 2


class _AnthropicCfg(_Cfg):
    glm_base_url = "https://open.bigmodel.cn/api/anthropic"


def test_anthropic_endpoint_protocol(monkeypatch):
    """base_url 以 /anthropic 结尾时走 /v1/messages，system 抽出、文本拼接。"""
    captured = {}

    def fake_post(url, **kw):
        captured["url"] = url
        captured["body"] = kw["json"]
        return _Resp(200, {"content": [{"type": "text", "text": "你好"}, {"type": "text", "text": "世界"}]})

    monkeypatch.setattr(summarizer.httpx, "post", fake_post)
    out = summarizer._chat(
        _AnthropicCfg(),
        [
            {"role": "system", "content": "你是总结助手"},
            {"role": "user", "content": "总结一下"},
        ],
    )
    assert out == "你好世界"
    assert captured["url"].endswith("/v1/messages")
    assert captured["body"]["system"] == "你是总结助手"
    assert captured["body"]["messages"] == [{"role": "user", "content": "总结一下"}]


def test_detailed_flag_switches_prompt(monkeypatch):
    """--detailed 应切换到详尽版 system prompt。"""
    captured = {}

    def fake_chat(cfg, messages):
        captured["system"] = messages[0]["content"]
        return "ok"

    monkeypatch.setattr(summarizer, "_chat", fake_chat)
    summarizer.summarize("[00:01] 内容", "标题", _Cfg())
    assert captured["system"] == summarizer.SYSTEM_PROMPT
    summarizer.summarize("[00:01] 内容", "标题", _Cfg(), detailed=True)
    assert captured["system"] == summarizer.DETAILED_SYSTEM_PROMPT


def test_extract_mindmap():
    md = (
        "## 一句话总结\n这是总结。\n\n"
        "## 核心要点\n- a\n\n"
        "## 思维导图\n- 分支1\n  - 子1\n- 分支2\n"
    )
    assert summarizer.extract_mindmap(md) == "- 分支1\n  - 子1\n- 分支2"
    # 无导图段 / 空串 → None
    assert summarizer.extract_mindmap("没有导图") is None
    assert summarizer.extract_mindmap("") is None
    assert summarizer.extract_mindmap(None) is None
    # 导图段后面还有别的 ## 标题时截断到那里
    md2 = "## 思维导图\n- x\n\n## 附录\n- y\n"
    assert summarizer.extract_mindmap(md2) == "- x"
