"""BV 号 / URL 解析测试。"""

import pytest

from biliparser.bilibili import BiliError, parse_bvid

BV = "BV1GJ411x7h7"


@pytest.mark.parametrize(
    "text",
    [
        BV,
        f"https://www.bilibili.com/video/{BV}",
        f"https://www.bilibili.com/video/{BV}?p=2&share_source=copy_web",
        f"http://m.bilibili.com/video/{BV}",
        f"www.bilibili.com/video/{BV}/?spm_id_from=333",
        f"看看这个 {BV} 很有意思",
    ],
)
def test_parse_bvid_valid(text):
    assert parse_bvid(text) == BV


def test_b23_short_link_rejected_with_hint():
    with pytest.raises(BiliError) as e:
        parse_bvid("https://b23.tv/abcd123")
    assert "短链" in str(e.value)
    assert e.value.hint


def test_av_number_rejected():
    with pytest.raises(BiliError, match="av 号"):
        parse_bvid("https://www.bilibili.com/video/av170001")


def test_garbage_input_rejected():
    with pytest.raises(BiliError, match="无法从输入中解析"):
        parse_bvid("hello world")


def test_wrong_length_bv_rejected():
    # BV 后不足 10 位，不算合法 BV 号
    with pytest.raises(BiliError):
        parse_bvid("BV1GJ411x7")
