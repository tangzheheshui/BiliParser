"""降级模式：元数据 + 热评上下文拼装测试。"""

from biliparser.meta import COMMENT_CHARS, build_meta_context


def _info(**kw):
    base = {
        "title": "测试视频",
        "desc": "这是简介",
        "duration": 310,
        "owner": {"name": "某UP"},
        "stat": {"view": 1000, "like": 100, "coin": 10, "danmaku": 5},
        **kw,
    }
    return base


def test_basic_fields_and_tags():
    ctx = build_meta_context(_info(), ["乡村", "vlog"], [])
    assert "UP 主：某UP" in ctx
    assert "时长：310 秒" in ctx
    assert "播放 1000" in ctx
    assert "简介：这是简介" in ctx
    assert "标签：乡村、vlog" in ctx
    assert "热门评论" not in ctx  # 无评论时不输出该节


def test_comments_with_likes_and_replies():
    comments = [
        {"message": "主干评论", "like": 59, "pinned": False, "sub": ["楼中楼", ""]},
        {"message": "置顶评论", "like": 1000, "pinned": True, "sub": []},
    ]
    ctx = build_meta_context(_info(), [], comments)
    assert "- (赞 59) 主干评论" in ctx
    assert "- ↳" not in ctx and "↳ 楼中楼" in ctx  # 空回复被跳过
    assert "[置顶] 置顶评论" in ctx


def test_long_comment_clipped():
    comments = [{"message": "长" * 500, "like": 1, "pinned": False, "sub": []}]
    ctx = build_meta_context(_info(), [], comments)
    line = [l for l in ctx.splitlines() if l.startswith("- (赞 1)")][0]
    assert COMMENT_CHARS <= len(line) < 500


def test_missing_optional_fields():
    ctx = build_meta_context({"title": "t", "duration": 0, "owner": {}}, [], [])
    assert "UP 主：未知" in ctx
    assert "简介" not in ctx and "数据" not in ctx and "标签" not in ctx
