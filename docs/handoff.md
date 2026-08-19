# 换机续作指南（2026-08-19 收工状态）

> 明天可能在另一台电脑上继续开发。本文档随仓库走，包含：当前进度、
> 新机器环境搭建、以及明天第一件事。

## 当前进度（一句话）

桌面版 + 激活码 + 授权服务器已全链路跑通（含 macOS 打包）；正常视频
3~10 秒出正确总结；**剩最后一块：新视频的语音转写（ASR）兜底**。

## 新机器环境搭建

```bash
git clone git@github.com:tangzheheshui/BiliParser.git && cd BiliParser

# Python 3.12+（Mac: /opt/homebrew/bin/python3；没有 uv 也能跑）
python3 -m venv .venv
.venv/bin/pip install -e . pytest faster-whisper

# 授权服务器（本地联调用，独立 venv）
cd license-server && python3 -m venv .venv && .venv/bin/pip install flask httpx pytest && cd ..

# 桌面打包（可选）
bash packaging/build-macos.sh   # 或带参数：… https://lic.example.com 出发行版
```

## 不随仓库走的东西（新机器要重新配）

| 文件（都在 `~/.biliparser/`） | 作用 | 怎么补 |
|---|---|---|
| `config.toml` | SESSDATA + GLM key（**注意 key 的额度在 Anthropic 端点，paas/v4 会报余额不足**） | 从旧机器拷，或按 README 重新填 |
| `license.json` | 本机激活凭证（绑设备指纹） | 新机器重新激活 |
| `seen_subs.json` | 跨视频字幕串台指纹库 | 可不拷，重新积累 |
| `models/` | whisper 模型 | 首次运行自动下载 |

## 明天第一件事：修 ASR 模型下载

`--asr` 已实现但模型下载失败：huggingface.co 连不上。修复：

```bash
export HF_ENDPOINT=https://hf-mirror.com    # 国内镜像
.venv/bin/biliparse BV1xkgn6hEqe --asr      # 首次下载 base 模型 141MB 并实测
```

通了之后：① web 加 `/api/transcribe` 路由 + 前端下拉「语音转写」选项
（仅串台视频用，正常视频仍走字幕秒出）② 重新打包。

## 背景速读（串台问题是什么）

B 站 2026 年起对第三方请求的字幕链路收紧，新发布 1~2 天的视频 CDN 会
返回**完全不相干的字幕**（实测一个视频拉到过 9 种别人的内容）。已上
四道防线见 `bilibili.py` 的 `fetch_full_subtitle`；LLM 语义校验试过并
放弃，教训在 `summarizer.py` 注释里。测试串台样本：`BV1xkgn6hEqe`。

## 授权服务器本地联调

```bash
cd license-server
GLM_API_KEY=<智谱key> GLM_MODEL=glm-4-flash .venv/bin/python app.py   # :7900
open "http://127.0.0.1:7900/admin?key=dev-admin"                        # 生成激活码
BILIPARSER_LICENSE_SERVER=http://127.0.0.1:7900 ../.venv/bin/biliparse-web  # 发行模式工作台
```
