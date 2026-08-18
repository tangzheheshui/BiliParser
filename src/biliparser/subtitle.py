"""字幕选择与文本拼装。"""

# 语言优先级：UP 上传的中文字幕（zh-CN/zh-Hans）> AI 中文字幕（ai_zh/ai-zh，两种写法都出现过）
_PRIORITY = ["zh-CN", "zh-Hans", "ai_zh", "ai-zh"]


def pick_subtitle(subtitles: list[dict], lang: str | None = None) -> dict | None:
    """按优先级挑一条字幕；显式指定 lang 时精确匹配 lan 字段。

    找不到返回 None，由调用方给出「可选语言列表」提示。
    """
    if not subtitles:
        return None
    if lang:
        for s in subtitles:
            if s.get("lan") == lang:
                return s
        return None
    for want in _PRIORITY:
        for s in subtitles:
            if s.get("lan") == want:
                return s
    for s in subtitles:  # 其他中文变体（zh-Hant 等）
        if str(s.get("lan", "")).startswith("zh"):
            return s
    return subtitles[0]  # 实在没有中文，退而求其次


def available_langs(subtitles: list[dict]) -> list[str]:
    """全部可选语言代码（用于错误提示）。"""
    return [str(s.get("lan", "?")) for s in subtitles]


def format_ts(seconds: float) -> str:
    """秒 → mm:ss（超一小时为 h:mm:ss）。"""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def build_transcript(lines: list[dict]) -> str:
    """字幕行 → 带时间戳的纯文本，每行 [mm:ss] 内容。

    保留逐行时间戳是为了让 LLM 能生成章节时间线。
    """
    return "\n".join(
        f"[{format_ts(l.get('from', 0))}] {l.get('content', '')}" for l in lines
    )
