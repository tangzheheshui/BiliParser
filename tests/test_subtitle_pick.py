"""字幕选择优先级与文本拼装测试。"""

from biliparser.subtitle import (
    available_langs,
    build_transcript,
    format_ts,
    pick_subtitle,
    prioritize,
)


def _sub(lan, **kw):
    return {"lan": lan, "lan_doc": kw.pop("lan_doc", lan), "subtitle_url": "//x/y.json", **kw}


def test_prioritize_order():
    subs = [_sub("en"), _sub("ai-zh"), _sub("zh-Hans"), _sub("zh-Hant")]
    assert [s["lan"] for s in prioritize(subs)] == ["zh-Hans", "ai-zh", "zh-Hant", "en"]


def test_cc_preferred_over_ai():
    subs = [_sub("ai_zh"), _sub("zh-CN")]
    assert pick_subtitle(subs)["lan"] == "zh-CN"


def test_priority_order():
    assert pick_subtitle([_sub("en"), _sub("ai_zh")])["lan"] == "ai_zh"
    assert pick_subtitle([_sub("zh-Hans"), _sub("ai_zh")])["lan"] == "zh-Hans"


def test_other_chinese_variant_fallback():
    assert pick_subtitle([_sub("en"), _sub("zh-Hant")])["lan"] == "zh-Hant"


def test_any_language_last_resort():
    assert pick_subtitle([_sub("en"), _sub("ja")])["lan"] == "en"


def test_explicit_lang_exact_match():
    subs = [_sub("zh-CN"), _sub("ai_zh")]
    assert pick_subtitle(subs, "ai_zh")["lan"] == "ai_zh"
    assert pick_subtitle(subs, "ko") is None


def test_empty_returns_none():
    assert pick_subtitle([]) is None
    assert pick_subtitle([], "zh-CN") is None


def test_available_langs():
    assert available_langs([_sub("zh-CN"), _sub("ai_zh")]) == ["zh-CN", "ai_zh"]


def test_format_ts():
    assert format_ts(0) == "00:00"
    assert format_ts(65) == "01:05"
    assert format_ts(3599) == "59:59"
    assert format_ts(3600) == "1:00:00"
    assert format_ts(3671) == "1:01:11"


def test_build_transcript():
    lines = [
        {"from": 0, "to": 2, "content": "大家好"},
        {"from": 63, "to": 66, "content": "今天讲讲"},
    ]
    assert build_transcript(lines) == "[00:00] 大家好\n[01:03] 今天讲讲"
