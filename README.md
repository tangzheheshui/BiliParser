# BiliParser

B 站视频字幕提取 + AI 总结命令行工具——输入一个视频链接，输出结构化摘要（一句话总结 / 核心要点 / 章节时间线 / 关键词）。

自用工具：走 B 站 web 接口拉取现成字幕（UP 上传的 CC 字幕或官方 AI 字幕），交给智谱 GLM 总结。**不含语音转写**；拿不到字幕时（未配 SESSDATA 或视频无字幕）自动降级为「元数据 + 热评」的推断性总结。

## 安装

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)：

```bash
cd BiliParser
uv sync
```

## 配置

首次使用前创建 `~/.biliparser/config.toml`（Windows 即 `C:\Users\<你>\.biliparser\config.toml`）：

```toml
# B 站登录 Cookie（走完整字幕总结必需；不配则自动降级为元数据+热评总结）
sessdata = "你的SESSDATA"

[glm]
# 智谱 API Key
api_key = "你的key"
# 模型可换 glm-5.2 等，见 https://bigmodel.cn/pricing
model = "glm-4.7"
base_url = "https://open.bigmodel.cn/api/paas/v4/"
```

**SESSDATA 怎么拿**：电脑浏览器登录 [bilibili.com](https://www.bilibili.com) → 按 F12 打开开发者工具 → 「应用」标签 → 左侧 Cookie → 找到 `SESSDATA`，复制它的值（一长串字符）。注意它约一个月过期，失效了重新复制一次即可。

**GLM API Key 怎么拿**：注册 [bigmodel.cn](https://bigmodel.cn) → 控制台 → API Keys。

所有配置也可用环境变量覆盖：`BILI_SESSDATA`、`ZHIPUAI_API_KEY`（或 `GLM_API_KEY`）、`BILIPARSER_MODEL`、`BILIPARSER_BASE_URL`。

## 用法

```bash
# 总结一个视频（链接或 BV 号都行）
uv run biliparse https://www.bilibili.com/video/BV1xx411c7mD

# 多 P 视频指定分 P
uv run biliparse BV1xx411c7mD --page 2

# 只看字幕全文（不花 LLM 的钱，调试用）
uv run biliparse BV1xx411c7mD --subtitle-only

# 详尽版总结（不漏话题、保留具体数字与金句，存为 <BV号>-详细.md）
uv run biliparse BV1xx411c7mD --detailed --save

# 保存为 Markdown 文件
uv run biliparse BV1xx411c7mD --save          # 存为 <BV号>.md
uv run biliparse BV1xx411c7mD --save 总结.md

# 指定字幕语言（默认优先中文字幕：CC > AI）
uv run biliparse BV1xx411c7mD --lang ai_zh
```

### 降级模式：元数据 + 热评总结

只配 `glm.api_key` 不配 `sessdata` 也能用：此时拿不到字幕，工具会自动拉取公开的标签和热门评论，让 AI 输出**推断性**总结（一句话总结 / 核心要点 / 评论区看点 / 关键词）。配好 `sessdata` 后自动回到完整字幕总结。

## Web 工作台

```bash
uv run biliparse-web            # http://127.0.0.1:7842
```

三栏工作区：左侧操作按钮与配置状态，中间视频链接与预览，右侧文本分析
（原始字幕 / 标准总结 / 详尽总结 / 元数据+热评 四个页签，支持复制与导出 .md）。
零新依赖（标准库起服务 + 单文件前端），需求与设计见 [docs/web-workspace.md](docs/web-workspace.md)。

## 开发

```bash
uv run pytest          # 离线单元测试
uv run biliparse --help
```

## 常见问题

- **提示「该视频没有可用字幕」**：纯音乐、方言较重或发布不久的视频常没有 AI 字幕，属正常限制（此时总结会自动降级为元数据+热评模式；`--subtitle-only` 仍会报错）。
- **提示「SESSDATA 未配置或已失效」**：按上文重新复制 SESSDATA（约一个月过期）。
- **HTTP 412**：请求头不完整会触发风控（本工具已内置完整浏览器请求头规避）；若仍出现说明请求过于频繁，等几分钟再用。

## 已知边界（MVP）

- 不做 ASR 语音转写（无字幕视频的兜底，后续版本考虑 faster-whisper）
- 不支持 b23.tv 短链、av 号、番剧/课程（epid 体系）
- 依赖 B 站未公开 web 接口，接口变更可能导致失效（2026 年初第三方库 bilibili-api-python 已因此停更，本项目用裸 HTTP 自实现，链路只有 3 个请求，坏了也好修）

## 路线图（可选增强）

- 无字幕视频：音频下载 + faster-whisper 本地转写
- B 站官方 AI 视频总结接口（`view/conclusion/get`）作为补充源
- 批量处理收藏夹/稍后再看
