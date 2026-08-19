"""fetch_full_subtitle：按覆盖时长挑最完整字幕（真实翻车案例：UP 的 CC 只有 1 行）。"""

from biliparser import bilibili


def _lines(n, step=5):
    return [{"from": i * step, "to": i * step + step, "content": "x"} for i in range(n)]


def test_prefers_fuller_ai_over_truncated_cc(monkeypatch):
    cc = {"lan": "zh-Hans", "lan_doc": "中文（UP）", "subtitle_url": "//x/cc.json"}
    ai = {"lan": "ai-zh", "lan_doc": "中文（自动）", "subtitle_url": "//x/ai.json"}
    files = {"//x/cc.json": _lines(1, 27), "//x/ai.json": _lines(62)}  # 27秒 vs 310秒
    monkeypatch.setattr(bilibili, "get_subtitle_info", lambda *a, **k: {"subtitles": [cc, ai]})
    monkeypatch.setattr(bilibili, "download_subtitle", lambda c, u: files[u])
    sub, lines, cov, consistent = bilibili.fetch_full_subtitle(None, "BV", 1, duration=310)
    assert sub["lan"] == "ai-zh"
    assert len(lines) == 62
    assert cov == 1.0


def test_cc_kept_when_complete(monkeypatch):
    cc = {"lan": "zh-Hans", "lan_doc": "中文", "subtitle_url": "//x/cc.json"}
    ai = {"lan": "ai-zh", "lan_doc": "中文（自动）", "subtitle_url": "//x/ai.json"}
    files = {"//x/cc.json": _lines(60), "//x/ai.json": _lines(30)}
    monkeypatch.setattr(bilibili, "get_subtitle_info", lambda *a, **k: {"subtitles": [ai, cc]})
    monkeypatch.setattr(bilibili, "download_subtitle", lambda c, u: files[u])
    sub, lines, cov, consistent = bilibili.fetch_full_subtitle(None, "BV", 1, duration=310)
    assert sub["lan"] == "zh-Hans" and cov > 0.9


def test_no_subtitles_returns_none(monkeypatch):
    monkeypatch.setattr(bilibili, "get_subtitle_info", lambda *a, **k: {"subtitles": []})
    assert bilibili.fetch_full_subtitle(None, "BV", 1, duration=100) == (None, [], 0, True)


def test_lang_filter(monkeypatch):
    ai = {"lan": "ai-zh", "lan_doc": "中文", "subtitle_url": "//x/ai.json"}
    monkeypatch.setattr(
        bilibili, "get_subtitle_info", lambda *a, **k: {"subtitles": [ai]}
    )
    sub, _, _, _ = bilibili.fetch_full_subtitle(None, "BV", 1, duration=100, lang="en")
    assert sub is None


def test_retry_when_coverage_poor(monkeypatch):
    """第一轮只有残缺字幕 → 不达标，第二轮拿到完整的。"""
    ai = {"lan": "ai-zh", "lan_doc": "中文", "subtitle_url": "//x/ai.json"}
    state = {"round": 0}

    def fake_info(*a, **k):
        state["round"] += 1
        return {"subtitles": [ai]}

    def fake_dl(c, u):
        return _lines(6) if state["round"] == 1 else _lines(62)

    monkeypatch.setattr(bilibili, "get_subtitle_info", fake_info)
    monkeypatch.setattr(bilibili, "download_subtitle", fake_dl)
    sub, lines, cov, consistent = bilibili.fetch_full_subtitle(None, "BV", 1, duration=310, rounds=2)
    assert len(lines) == 62 and cov == 1.0


def test_parse_page():
    assert bilibili.parse_page("https://www.bilibili.com/video/BV1xx?p=3&x=1") == 3
    assert bilibili.parse_page("https://www.bilibili.com/video/BV1xx") is None
    assert bilibili.parse_page("BV1xx") is None


def test_inconsistent_fingerprints_flagged(monkeypatch):
    """多轮拉到的字幕首行不同（串台）→ consistent=False。"""
    ai = {"lan": "ai-zh", "lan_doc": "中文", "subtitle_url": "//x/ai.json"}
    state = {"round": 0}

    def fake_info(*a, **k):
        state["round"] += 1
        return {"subtitles": [ai]}

    def fake_dl(c, u):
        n = state["round"]
        # 三轮分别返回三个"不同视频"的字幕（首行不同、都够长）
        return [{"from": 0, "to": 300, "content": f"第{n}个视频的开场白"}] + _lines(60)

    monkeypatch.setattr(bilibili, "get_subtitle_info", fake_info)
    monkeypatch.setattr(bilibili, "download_subtitle", fake_dl)
    sub, lines, cov, consistent = bilibili.fetch_full_subtitle(None, "BV", 1, duration=310, min_coverage=1.1, rounds=3)
    assert sub is not None and not consistent
