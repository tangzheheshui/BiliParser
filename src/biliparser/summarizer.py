"""AI（OpenAI 兼容接口）视频总结。"""

import time

import httpx

# 超过此字符数（约 2h+ 视频）走分块摘要再合并
MAX_CHARS = 50_000
CHUNK_CHARS = 40_000

SYSTEM_PROMPT = """你是专业的视频内容总结助手。用户会给你一份带时间戳的视频字幕文本，每行格式为 [mm:ss] 字幕内容。

请用中文输出 Markdown 格式的总结，结构固定为：

## 一句话总结
（一句话概括视频核心内容）

## 核心要点
- （5~8 条，按重要性排序）

## 章节时间线
- `[mm:ss]` 章节标题（用字幕中的时间戳把视频划分为 3~8 个章节）

## 关键词
`关键词1` `关键词2` `关键词3` ...

注意：章节时间线里的时间戳必须来自字幕原文，不要编造。直接输出总结正文，不要额外寒暄。"""

CHUNK_SYSTEM_PROMPT = (
    "你是视频内容总结助手。以下是长视频字幕的一部分，"
    "请用中文简洁列出该部分的要点（含关键时间戳，格式 [mm:ss]）。"
)


class SummarizeError(Exception):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


def _chat(cfg, messages: list[dict]) -> str:
    """调一次 chat/completions，429 重试一次。"""
    url = cfg.glm_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.glm_api_key}"}
    body = {"model": cfg.glm_model, "messages": messages, "temperature": 0.3}
    for attempt in (1, 2):
        try:
            resp = httpx.post(url, json=body, headers=headers, timeout=180)
        except httpx.HTTPError as e:
            raise SummarizeError(f"请求 AI 接口失败：{e.__class__.__name__}") from e
        if resp.status_code == 429 and attempt == 1:
            time.sleep(2)
            continue
        if resp.status_code == 401:
            raise SummarizeError(
                "API Key 无效（HTTP 401）", hint="请检查配置中的 glm.api_key"
            )
        if resp.status_code != 200:
            raise SummarizeError(f"AI 接口返回 HTTP {resp.status_code}：{resp.text[:200]}")
        try:
            return resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as e:
            raise SummarizeError(f"AI 返回格式异常：{resp.text[:200]}") from e
    raise SummarizeError("AI 接口限流（HTTP 429）", hint="请稍后重试")


def _split_transcript(transcript: str) -> list[str]:
    """按行边界把字幕切成不超过 CHUNK_CHARS 的块。"""
    chunks, cur, size = [], [], 0
    for line in transcript.splitlines():
        cur.append(line)
        size += len(line) + 1
        if size >= CHUNK_CHARS:
            chunks.append("\n".join(cur))
            cur, size = [], 0
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def summarize(transcript: str, video_title: str, cfg) -> str:
    """字幕 → Markdown 总结。超长字幕分块提取要点后合并。"""
    if len(transcript) <= MAX_CHARS:
        return _chat(
            cfg,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"视频标题：{video_title}\n\n字幕文本：\n{transcript}"},
            ],
        )

    # map：逐块提取要点
    chunks = _split_transcript(transcript)
    notes = []
    for i, chunk in enumerate(chunks, 1):
        notes.append(
            _chat(
                cfg,
                [
                    {"role": "system", "content": CHUNK_SYSTEM_PROMPT},
                    {"role": "user", "content": f"（第 {i}/{len(chunks)} 部分）\n{chunk}"},
                ],
            )
        )
    # reduce：合并成最终总结
    merged = "\n\n".join(f"### 第 {i} 部分要点\n{note}" for i, note in enumerate(notes, 1))
    return _chat(
        cfg,
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"视频标题：{video_title}\n\n以下是长视频各段要点（由字幕整理、含时间戳），"
                    f"请汇总为最终总结：\n\n{merged}"
                ),
            },
        ],
    )
