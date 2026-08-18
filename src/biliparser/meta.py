"""降级模式：无字幕时用公开元数据 + 热评拼装供 LLM 总结的上下文。"""

# 单条评论截断长度（表情代码如 [doge] 占字符但不占信息量）
COMMENT_CHARS = 200
MAX_COMMENTS = 20


def build_meta_context(
    info: dict, tags: list[str], comments: list[dict], max_comments: int = MAX_COMMENTS
) -> str:
    """视频信息 + 标签 + 热评 → 纯文本上下文，喂给 LLM 做降级总结。

    评论是观众视角，既提供内容线索也提供噪音，交给 LLM 自行甄别。
    """
    lines: list[str] = []
    stat = info.get("stat") or {}
    lines.append(f"UP 主：{(info.get('owner') or {}).get('name', '未知')}")
    lines.append(f"时长：{info.get('duration', 0)} 秒")
    if stat:
        lines.append(
            f"数据：播放 {stat.get('view', 0)}｜点赞 {stat.get('like', 0)}｜"
            f"硬币 {stat.get('coin', 0)}｜弹幕 {stat.get('danmaku', 0)}"
        )
    desc = str(info.get("desc") or "").strip()
    if desc:
        lines.append(f"简介：{desc}")
    if tags:
        lines.append(f"标签：{'、'.join(tags)}")

    if comments:
        lines.append("")
        lines.append("热门评论（按热度）：")
        for c in comments[:max_comments]:
            tag = "[置顶] " if c.get("pinned") else f"(赞 {c.get('like', 0)}) "
            lines.append(f"- {tag}{_clip(c['message'])}")
            for s in c.get("sub") or []:
                if s:
                    lines.append(f"  ↳ {_clip(s)}")
    return "\n".join(lines)


def _clip(text: str, limit: int = COMMENT_CHARS) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"
