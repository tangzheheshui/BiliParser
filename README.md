# BiliParser

B 站视频字幕提取 + AI 总结工具——输入一个视频链接，输出结构化摘要
（一句话总结 / 核心要点 / 章节时间线 / 关键词）。

自用工具：走 B 站 web 接口拉取现成字幕（UP 上传的 CC 字幕或官方 AI 字幕），
交给智谱 GLM 总结。**不含语音转写**；拿不到字幕时（未配 SESSDATA 或视频无字幕）
自动降级为「元数据 + 热评」的推断性总结。

一个 Python 包、三个入口：

| 命令 | 形态 | 说明 |
|---|---|---|
| `biliparse` | CLI | 命令行，自用 |
| `biliparse-web` | Web 工作台 | 本机三栏工作区（:7842） |
| `biliparser-desktop` | 桌面版 | pywebview 壳；发行模式走激活码 |

## 文档索引

| 文档 | 回答什么 | 什么时候看 |
|---|---|---|
| [docs/requirements/client.md](docs/requirements/client.md) | 客户端要做什么（CLI / Web 工作台 / 桌面版 / 激活） | 改客户端需求 |
| [docs/requirements/server.md](docs/requirements/server.md) | 授权服务器 + 网页版要做什么（含跨端决策） | 改服务器 / 网页版 |
| [docs/design/architecture.md](docs/design/architecture.md) | 怎么实现的（模块 / 字幕链路 / AI 链路 / 踩坑） | 改实现、排查 |
| [docs/operations/deploy.md](docs/operations/deploy.md) | 服务器怎么部署上线 | 部署 / 换服务器 |
| [docs/operations/admin-guide.md](docs/operations/admin-guide.md) | 管理后台怎么用（发码 / 售后） | 卖货 / 运营 |

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

# 只输出思维导图（随总结自动生成的缩进树）
uv run biliparse BV1xx411c7mD --mindmap

# 保存为 Markdown 文件
uv run biliparse BV1xx411c7mD --save          # 存为 <BV号>.md
uv run biliparse BV1xx411c7mD --save 总结.md

# 指定字幕语言（默认优先中文字幕：CC > AI）
uv run biliparse BV1xx411c7mD --lang ai_zh
```

### 降级模式：元数据 + 热评总结

只配 `glm.api_key` 不配 `sessdata` 也能用：此时拿不到字幕，工具会自动拉取公开的标签和热门评论，让 AI 输出**推断性**总结（一句话总结 / 核心要点 / 评论区看点 / 关键词）。配好 `sessdata` 后自动回到完整字幕总结。

## Web 工作台 / 桌面版

```bash
uv run biliparse-web                        # Web 工作台 http://127.0.0.1:7842
uv run biliparser-desktop                   # 桌面版（直连，自用）
uv run biliparser-desktop --server https://… # 发行模式（首启激活码）
```

需求见 [docs/requirements/client.md](docs/requirements/client.md)；发行模式与网页版
托管见 [docs/requirements/server.md](docs/requirements/server.md)；部署见
[docs/operations/deploy.md](docs/operations/deploy.md)。

## 开发

```bash
uv run pytest          # 客户端单元测试（tests/）
uv run biliparse --help
```

授权服务器是独立 venv（本地联调才需要装）：

```bash
cd license-server && python3 -m venv .venv && .venv/bin/pip install flask httpx pytest && cd ..
cd license-server && .venv/bin/python -m pytest      # 服务器测试
```

### 不随仓库走的东西（换机器 / 换环境要补）

| 文件（都在 `~/.biliparser/`） | 作用 | 怎么补 |
|---|---|---|
| `config.toml` | SESSDATA + GLM key | 按上文重新填 |
| `license.json` | 本机激活凭证（绑设备指纹） | 重新激活 |
| `seen_subs.json` | 跨视频字幕串台指纹库 | 可不补，重新积累 |
| `models/` | whisper 模型 | 首次运行自动下载 |

## 常见问题

- **提示「该视频没有可用字幕」**：纯音乐、方言较重或发布不久的视频常没有 AI 字幕，属正常限制（此时总结会自动降级为元数据+热评模式；`--subtitle-only` 仍会报错）。
- **提示「SESSDATA 未配置或已失效」**：按上文重新复制 SESSDATA（约一个月过期）。
- **HTTP 412**：请求头不完整会触发风控（本工具已内置完整浏览器请求头规避）；若仍出现说明请求过于频繁，等几分钟再用。
