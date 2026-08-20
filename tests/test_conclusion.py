"""get_conclusion（B 站官方 AI 总结兜底）+ bili_conclusion_markdown。"""

from biliparser import bilibili, summarizer


def test_conclusion_ok(monkeypatch):
    payload = {
        "code": 0,
        "data": {
            "code": 0,
            "model_result": {
                "summary": "这是一段总结",
                "outline": [
                    {"title": "开场", "timestamp": 1,
                     "part_outline": [{"timestamp": 5, "content": "要点A"}]}
                ],
            },
        },
    }
    monkeypatch.setattr(bilibili, "_get_wbi_keys", lambda c: ("a" * 32, "b" * 32))
    monkeypatch.setattr(bilibili, "_request_json", lambda *a, **k: payload)
    summary, outline = bilibili.get_conclusion(None, "BV1", 1, 123)
    assert summary == "这是一段总结"
    assert outline[0]["title"] == "开场"


def test_conclusion_queued_returns_none(monkeypatch):
    payload = {"code": 0, "data": {"code": 1, "model_result": {"summary": "", "outline": None}}}
    monkeypatch.setattr(bilibili, "_get_wbi_keys", lambda c: ("a" * 32, "b" * 32))
    monkeypatch.setattr(bilibili, "_request_json", lambda *a, **k: payload)
    assert bilibili.get_conclusion(None, "BV1", 1, 123) is None


def test_conclusion_unsupported_returns_none(monkeypatch):
    monkeypatch.setattr(bilibili, "_get_wbi_keys", lambda c: ("a" * 32, "b" * 32))
    monkeypatch.setattr(bilibili, "_request_json", lambda *a, **k: {"code": 0, "data": {"code": -1}})
    assert bilibili.get_conclusion(None, "BV1", 1, 123) is None


def test_conclusion_request_error_returns_none(monkeypatch):
    def boom(*a, **k):
        raise bilibili.BiliError("风控", code=412)

    monkeypatch.setattr(bilibili, "_get_wbi_keys", lambda c: ("a" * 32, "b" * 32))
    monkeypatch.setattr(bilibili, "_request_json", boom)
    assert bilibili.get_conclusion(None, "BV1", 1, 123) is None


def test_conclusion_no_up_mid():
    assert bilibili.get_conclusion(None, "BV1", 1, 0) is None


def test_conclusion_markdown():
    md = summarizer.bili_conclusion_markdown(
        "总结内容",
        [{"title": "开场", "timestamp": 65,
          "part_outline": [{"timestamp": 66, "content": "要点"}]}],
    )
    assert "## 一句话总结" in md and "总结内容" in md
    assert "## 章节时间线" in md
    assert "`01:05`" in md and "`01:06`" in md


def test_conclusion_markdown_no_outline():
    md = summarizer.bili_conclusion_markdown("总结内容", [])
    assert "## 一句话总结" in md and "## 章节时间线" not in md
