# 架构与实现原理

> 本文讲的是**客户端核心链路**（`src/biliparser/`：模块结构、字幕获取、AI 总结、
> 实测踩过的坑）；授权服务器（`license-server/`）的实现原理见
> [服务器需求](docs/requirements/server.md)。需求侧另见
> [客户端需求](docs/requirements/client.md)；部署见 [部署指南](docs/operations/deploy.md)。

## 一句话定位

输入一个 B 站视频链接 / BV 号，程序拉取**现成的字幕文件**（不是自己听视频
转写），交给 GLM 输出结构化总结。拿不到字幕时降级为「元数据 + 热评」的
推断性总结。

## 模块结构

```
src/biliparser/
├── bilibili.py     B 站 web API 封装（裸 HTTP 自实现，仅字幕链路所需请求）
├── wbi.py          wbi 签名（从 nav 的 wbi_img 提取密钥，对部分接口做参数签名）
├── subtitle.py     字幕选择（语言优先级）与文本拼装（带时间戳）
├── meta.py         元数据 + 热评上下文（降级模式的输入）
├── summarizer.py   GLM 总结（直连 / managed 服务器代理；超长视频 map-reduce）
├── config.py       配置加载（~/.biliparser/config.toml + 环境变量覆盖 + 烧入的默认服务器）
├── licensing.py    设备指纹 / 激活 / 凭证 / 验证 + 72h 离线宽限
├── desktop.py      pywebview 桌面壳（本地服务 + 原生窗口）
├── web.py          工作台 HTTP 服务（标准库起服，复用上面各模块）
├── cli.py          命令行入口
└── asr.py          语音转写兜底（faster-whisper，未做字幕时可选，非默认链路）
```

## 核心链路：字幕获取（3 个 HTTP 请求）

对，**就是调 B 站 web API**——但调的不是官方开放 API，而是 B 站网页播放器
自己用的**未公开内部接口**。裸 HTTP 自实现（第三方库 `bilibili-api-python`
已于 2026-01 停更），链路刻意保持短，坏了也好修。

```
1. GET /x/web-interface/view?bvid=<BV>
   → 视频元数据：aid、cid、标题、UP 主、时长、分 P 列表（无需登录）

2. GET /x/player/v2?bvid=&cid=     （或 wbi 签名的 /x/player/wbi/v2）
   → 字幕列表 subtitle.subtitles[]：每条带 lan（语言）和 subtitle_url

3. 下载 subtitle_url（//aisubtitle.h5.cn/... 或 //i0.hdslb.com/...，不在 api 域下）
   → 字幕 JSON 的 body 数组，每行 {from, to, content}（起止秒 + 文本）
```

对应代码：`bilibili.py` 的 `get_video_info` / `get_subtitle_info` /
`download_subtitle`，拼装见 `subtitle.py` 的 `build_transcript`（每行
`[mm:ss] 内容`，保留时间戳是为了让 LLM 能生成章节时间线）。

### 字幕从哪来（两类，都是 B 站现成的）

| 来源 | lan 字段 | 说明 |
|---|---|---|
| UP 上传的 CC 字幕 | `zh-CN` / `zh-Hans` | 创作者自己传的字幕 |
| B 站官方 AI 字幕 | `ai_zh` / `ai-zh` | B 站**自己**语音识别生成，程序直接下载其结果 |

关键：**语音识别是 B 站做的，本程序不做 ASR**。默认按语言优先级挑选
（CC > AI），但实际由 `fetch_full_subtitle` 先下载候选、按覆盖时长挑
「最完整」的一条，同分才按语言优先级（详见下节坑 1）。

### 为什么需要 SESSDATA

不登录时 B 站不返回 AI 字幕列表，所以拿 AI 字幕要有 SESSDATA（放 Cookie，
只作用于 B 站请求）。没有 SESSDATA 或视频无字幕时走降级模式。

## 降级链路：元数据 + 热评

只配 GLM key、不配 SESSDATA，或视频本就没有字幕时，程序拉取公开数据：

- `GET /x/tag/archive/tags` → 视频标签
- `GET /x/v2/reply/main` → 热门评论（按赞排序，含置顶，未登录约 20 条）

交给 GLM 输出**推断性**总结（一句话 / 核心要点 / 评论区看点 / 关键词），
前端会加「推断性结果」警示条。

## AI 总结链路

- **直连模式**（自用）：本地 `glm.api_key` 直接调智谱 GLM。
- **发行模式**（售卖）：`config` 里 `managed.server_url` 有值 → AI 调用改走
  授权服务器的 `/api/ai/chat` 代理。服务器持有 GLM key、按激活码做每日配额，
  客户端只带 token 请求头。B 站请求始终走用户本机（避免同 IP 风控）。
  见 [licensing.md](licensing.md)。

超长视频用 map-reduce 分段总结后合并（`summarizer.py`）。

## 数据流总览

```
输入 BV/链接
   │  parse_bvid（解析 BV，拒绝 b23 短链 / av 号）
   ▼
get_video_info ────────────────► 元数据（title/up/duration/cid）
   │
   ▼
get_subtitle_info（重试×4，wbi 签名轮询）──► 字幕列表
   │
   ▼
download_subtitle（逐条下载，按覆盖时长选最完整）
   │
   ├─ 有字幕 ──► build_transcript（带时间戳文本）──► GLM 总结 ──► 输出
   │
   └─ 无字幕 ──► 标签 + 热评 ──► GLM 推断性总结 ──► 输出
```

## 实测踩过的坑（都已在代码里兜住）

1. **残缺 CC 被优先选中**：UP 后补的 CC 可能只有 1 行 / 27 秒，语言优先级
   规则（CC > AI）会无脑选它。→ 改按覆盖时长挑最完整，同分才看语言。
2. **风控 HTTP 412**：`view` 接口只带 UA+Referer 会被拦，需补齐完整浏览器
   请求头（`bilibili.py` 的 `BROWSER_HEADERS`，无指纹 Cookie）。
3. **字幕列表多机不一致**：同一请求约 1/3 概率返回空，网页播放器靠重试
   拿到 → 程序按 `[wbi 签名 / 不签名] × attempts` 轮重试。
4. **AI 字幕 CDN 版本彩票**：同视频每次新签名 URL 随机落到不同节点，返回
   1% ~ 79% 的不同版本 → best-of-8 轮重试，跨轮保留最佳，覆盖率 ≥80% 收手。
5. **字幕「串台」**：新发布视频的 CDN 会返回**完全不属于本视频**的字幕
   （实测一个视频拉到过 9 种别人的内容）→ 内容指纹库（`seen_subs.json`）
   + 首行一致性检测；命中即拒或警告。LLM 语义校验试过并放弃（教训在
   `summarizer.py` 注释里）；测试串台样本：`BV1xkgn6hEqe`。

## 边界与风险

- 依赖 B 站**未公开**接口，无官方保证，接口变更可能失效（好在链路只有
  3 个请求，坏了好修）。
- 无字幕的视频默认不做 ASR（后续版本考虑 faster-whisper，代码已有 `asr.py`）。
- 不支持 b23.tv 短链、av 号、番剧/课程（epid 体系）。
