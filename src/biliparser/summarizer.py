"""AI（OpenAI 兼容接口）视频总结。"""

import time

import httpx

from . import subtitle

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

## 思维导图
- 一级分支（3~6 个，概括视频的几大主题）
  - 二级要点（每个分支下 2~4 个，两个空格缩进）

注意：思维导图用缩进列表表示层级（一级分支顶格 `-`，二级两个空格缩进），
节点文字 3~12 字、短小精悍，不要写整句，层级最多 3 级。

注意：章节时间线里的时间戳必须来自字幕原文，不要编造。
若怀疑字幕与视频不同源（字幕讲的内容领域与标题/简介完全不同，如游戏解说配乡村 vlog），在总结最前面输出一行「⚠ 疑似字幕串台：字幕实际讲的是……」，然后照常输出总结（不要因此拒绝总结；标题与字幕只是侧重不同不算串台）。直接输出总结正文，不要额外寒暄。"""

# Web 工作台「系统默认」版：与标准总结同结构，但去掉「## 思维导图」段
# （思维导图页签已移除，不再浪费 token 生成）。CLI 仍用上面的 SYSTEM_PROMPT。
SYSTEM_PROMPT_NO_MINDMAP = """你是专业的视频内容总结助手。用户会给你一份带时间戳的视频字幕文本，每行格式为 [mm:ss] 字幕内容。

请用中文输出 Markdown 格式的总结，结构固定为：

## 一句话总结
（一句话概括视频核心内容）

## 核心要点
- （5~8 条，按重要性排序）

## 章节时间线
- `[mm:ss]` 章节标题（用字幕中的时间戳把视频划分为 3~8 个章节）

## 关键词
`关键词1` `关键词2` `关键词3` ...

注意：章节时间线里的时间戳必须来自字幕原文，不要编造。
若怀疑字幕与视频不同源（字幕讲的内容领域与标题/简介完全不同，如游戏解说配乡村 vlog），在总结最前面输出一行「⚠ 疑似字幕串台：字幕实际讲的是……」，然后照常输出总结（不要因此拒绝总结；标题与字幕只是侧重不同不算串台）。直接输出总结正文，不要额外寒暄。"""

CHUNK_SYSTEM_PROMPT = (
    "你是视频内容总结助手。以下是长视频字幕的一部分，"
    "请用中文简洁列出该部分的要点（含关键时间戳，格式 [mm:ss]）。"
)

# --detailed 模式：详尽完整版，硬性要求不漏话题、保留具体数字
DETAILED_SYSTEM_PROMPT = """你是专业的视频内容详细总结助手。用户给你一份带时间戳的视频字幕（每行 [mm:ss] 内容）。

要求输出**详尽完整**的中文 Markdown 总结，硬性规则：字幕里每一个独立话题都必须覆盖，不允许合并省略；尽量保留原文中的具体数字、价格、涨跌幅、公司名、事件名、人名。结构：

## 视频概览
（3~5 句：这是什么类型的视频、博主是谁、整体讲了什么、结论基调）

## 逐段详解
（按时间线分 6~10 段；每段格式：`[mm:ss]` 小标题，下面 3~6 句详述该段内容，时间戳取自字幕原文）

## 关键数据与事实
- （把字幕中出现的所有具体数字/金额/价格/日期/公司/产品逐条列出）

## 博主核心观点
- （每条：观点 + 他给出的支撑逻辑/态度强弱，标注「明确看好/明确看空/中性观察/自嘲吐槽」）

## 金句摘录
- `[mm:ss]` 「原文」（有记忆点的原话，3~6 条，没有就省略本节）

## 思维导图
- 一级分支（3~6 个，概括视频的几大主题）
  - 二级要点（每个分支下 2~4 个，两个空格缩进）

（思维导图用缩进列表表示层级，节点文字 3~12 字，层级最多 3 级。）

直接输出正文，不要寒暄。若怀疑字幕与视频不同源（内容领域完全不同），在开头输出一行「⚠ 疑似字幕串台：字幕实际讲的是……」然后照常输出总结，不要拒绝总结。"""

# 降级模式：拿不到字幕，只有元数据 + 热评
META_SYSTEM_PROMPT = """你是视频内容总结助手。这次没有视频字幕，只有视频的公开信息（标题、简介、标签、数据）和热门评论。评论是观众视角，既有内容线索也有玩笑和噪音，请自行甄别。

请用中文输出 Markdown 格式的总结，结构固定为：

## 一句话总结
（一句话概括视频最可能的内容）

## 核心要点
- （3~6 条，按可信度排序）

## 评论区看点
- （2~4 条：观众在讨论/玩什么梗）

## 关键词
`关键词1` `关键词2` ...

注意：材料有限，总结是推断性的——把把握大的放前面，不确定的不要写成事实，完全无从判断的就明说。直接输出总结正文，不要额外寒暄。"""


MINDMAP_HEADING = "## 思维导图"


def bili_conclusion_markdown(summary: str, outline: list) -> str:
    """把 B 站官方 AI 总结拼成 Markdown（无字幕时的兜底，格式对齐标准总结）。

    outline 为 [{title, timestamp, part_outline:[{timestamp, content}]}]。
    """
    parts = ["## 一句话总结\n" + summary]
    if outline:
        lines = ["## 章节时间线"]
        for seg in outline:
            t = subtitle.format_ts(seg.get("timestamp") or 0)
            lines.append(f"- `{t}` {seg.get('title') or ''}")
            for pt in seg.get("part_outline") or []:
                pt_t = subtitle.format_ts(pt.get("timestamp") or 0)
                lines.append(f"  - `{pt_t}` {pt.get('content') or ''}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def extract_mindmap(md: str) -> str | None:
    """从总结 Markdown 里抽出「## 思维导图」段的缩进列表原文（不含标题行）。

    该段约定为最后一段；若后面还有别的 ## 标题则截到那里。无此段返回 None。
    """
    if not md:
        return None
    idx = md.find(MINDMAP_HEADING)
    if idx < 0:
        return None
    body = md[idx + len(MINDMAP_HEADING):]
    nxt = body.find("\n## ")
    if nxt >= 0:
        body = body[:nxt]
    text = body.strip()
    return text or None


class SummarizeError(Exception):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


# 串台检测教训（2026-08，勿重蹈）：曾试过「LLM 语义校验字幕是否属于本视频」，
# 两个致命伤：①只让模型吐一个字时它不给推理空间、几乎总答「是」；
# ②放进主路径后 3 轮重拉 + 逐次校验把总结拖到 30s+。现行方案见
# bilibili.fetch_full_subtitle：只做零耗时的确定性检查（字幕时长不得超视频、
# 跨视频重复指纹、覆盖率），串台视频秒级失败，不拖慢正常视频。


def _is_anthropic_endpoint(cfg) -> bool:
    """base_url 指向 Anthropic 兼容端点（GLM Coding Plan，如 Claude Code 在用）。"""
    return cfg.glm_base_url.rstrip("/").endswith("/anthropic")


def _error_detail(resp) -> str:
    """从错误响应里尽力抽出人话（OpenAI 风格与 Anthropic 风格都试）。"""
    try:
        err = resp.json().get("error") or {}
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    except ValueError:
        pass
    return resp.text[:200]


def _chat_anthropic(cfg, messages: list[dict]) -> str:
    """Anthropic Messages 协议（/v1/messages）：system 从 messages 里抽出来。"""
    url = cfg.glm_base_url.rstrip("/") + "/v1/messages"
    headers = {
        "x-api-key": cfg.glm_api_key,
        "Authorization": f"Bearer {cfg.glm_api_key}",
        "anthropic-version": "2023-06-01",
    }
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    body = {
        "model": cfg.glm_model,
        "max_tokens": 8192,
        "system": system,
        "messages": [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m["role"] != "system"
        ],
    }
    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=180)
    except httpx.HTTPError as e:
        raise SummarizeError(f"请求 AI 接口失败：{e.__class__.__name__}") from e
    if resp.status_code == 401:
        raise SummarizeError("API Key 无效（HTTP 401）", hint="请检查配置中的 glm.api_key")
    if resp.status_code != 200:
        detail = _error_detail(resp)
        if "余额不足" in detail or "无可用资源包" in detail:
            raise SummarizeError(
                f"AI 调用失败：{detail}",
                hint="请到 https://open.bigmodel.cn 充值或领取资源包后重试",
            )
        raise SummarizeError(f"AI 接口返回 HTTP {resp.status_code}：{detail}")
    try:
        return "".join(
            block["text"] for block in resp.json()["content"] if block.get("type") == "text"
        )
    except (KeyError, TypeError, ValueError) as e:
        raise SummarizeError(f"AI 返回格式异常：{resp.text[:200]}") from e


def _chat_managed(cfg, messages: list[dict]) -> str:
    """发行版模式：AI 调用经授权服务器代理（服务器持有 GLM key，按码限流）。"""
    from . import licensing  # 延迟导入，CLI 直连模式不依赖

    base = cfg.managed_server.rstrip("/")
    try:
        # 网页托管版把会话 token 直接挂在 cfg 上（无本地凭证文件）
        headers = getattr(cfg, "managed_auth", None) or licensing.auth_header()
        resp = httpx.post(
            base + "/api/ai/chat",
            json={"messages": messages, "temperature": 0.3},
            headers=headers, timeout=180,
        )
    except licensing.LicensingError as e:
        raise SummarizeError(str(e), hint=e.hint) from e
    except httpx.HTTPError as e:
        raise SummarizeError(
            f"连不上授权服务器（{e.__class__.__name__}）", hint="请检查网络后重试"
        ) from e
    if resp.status_code != 200:
        try:
            detail = resp.json().get("error") or resp.text[:200]
            hint = resp.json().get("hint")
        except ValueError:
            detail, hint = resp.text[:200], None
        if resp.status_code == 403:
            hint = hint or "授权已失效，请重新激活"
        raise SummarizeError(f"AI 调用失败：{detail}", hint=hint)
    try:
        return resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as e:
        raise SummarizeError(f"AI 返回格式异常：{resp.text[:200]}") from e


def _chat(cfg, messages: list[dict]) -> str:
    """调一次对话接口；真限流时重试一次。

    模式优先级：用户自有 key 直连（Anthropic 兼容 / OpenAI 兼容）>
    授权服务器代理（发行版默认：服务器免费模型 + 每码配额）。即用户在
    设置里配了自己的 key 就用自己的（运营方零成本），没配走服务器。
    注意：智谱余额不足（错误码 1113）也返回 HTTP 429，不能重试，
    要把「请充值」透出给用户。
    """
    if getattr(cfg, "glm_api_key", ""):
        if _is_anthropic_endpoint(cfg):
            return _chat_anthropic(cfg, messages)
    elif getattr(cfg, "managed_server", ""):
        return _chat_managed(cfg, messages)

    url = cfg.glm_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.glm_api_key}"}
    body = {"model": cfg.glm_model, "messages": messages, "temperature": 0.3}
    for attempt in (1, 2):
        try:
            resp = httpx.post(url, json=body, headers=headers, timeout=180)
        except httpx.HTTPError as e:
            raise SummarizeError(f"请求 AI 接口失败：{e.__class__.__name__}") from e
        if resp.status_code == 401:
            raise SummarizeError(
                "API Key 无效（HTTP 401）", hint="请检查配置中的 glm.api_key"
            )
        if resp.status_code != 200:
            detail = _error_detail(resp)
            if "余额不足" in detail or "无可用资源包" in detail:
                raise SummarizeError(
                    f"AI 调用失败：{detail}",
                    hint="请到 https://open.bigmodel.cn 充值或领取资源包后重试",
                )
            if resp.status_code == 429 and attempt == 1:
                time.sleep(2)  # 真限流，稍候重试一次
                continue
            raise SummarizeError(f"AI 接口返回 HTTP {resp.status_code}：{detail}")
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


def summarize_meta(meta_context: str, video_title: str, cfg) -> str:
    """降级模式：元数据 + 热评上下文 → Markdown 总结。"""
    return _chat(
        cfg,
        [
            {"role": "system", "content": META_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"视频标题：{video_title}\n\n公开信息与热门评论：\n{meta_context}",
            },
        ],
    )


def _summarize_with(transcript: str, video_title: str, cfg, final_prompt: str) -> str:
    """字幕 → Markdown：短字幕单次调用，长字幕 map-reduce 后合并。"""
    if len(transcript) <= MAX_CHARS:
        return _chat(
            cfg,
            [
                {"role": "system", "content": final_prompt},
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
            {"role": "system", "content": final_prompt},
            {
                "role": "user",
                "content": (
                    f"视频标题：{video_title}\n\n以下是长视频各段要点（由字幕整理、含时间戳），"
                    f"请汇总为最终总结：\n\n{merged}"
                ),
            },
        ],
    )


def summarize(transcript: str, video_title: str, cfg, detailed: bool = False,
              include_mindmap: bool = True) -> str:
    """字幕 → Markdown 总结（标准 / 详尽模板）。

    include_mindmap=False 时标准总结不带「## 思维导图」段（Web 工作台用；
    CLI 默认 True 保持原样）。详尽模式不受此参数影响。
    """
    if detailed:
        prompt = DETAILED_SYSTEM_PROMPT
    else:
        prompt = SYSTEM_PROMPT if include_mindmap else SYSTEM_PROMPT_NO_MINDMAP
    return _summarize_with(transcript, video_title, cfg, prompt)


def summarize_custom(transcript: str, video_title: str, cfg, system_prompt: str) -> str:
    """字幕 → 用户自定义提示词的总结（Web 工作台自定义模板）。"""
    return _summarize_with(transcript, video_title, cfg, system_prompt)
