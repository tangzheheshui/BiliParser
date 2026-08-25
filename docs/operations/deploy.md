# 授权服务器部署指南（license-server/，v2）

> 执行步骤已做成 skill（`.claude/skills/deploy/`），说一句 `/deploy` 让 Claude 照着跑。
> 本页只记「决策 + 风险 + 为什么」，供人读；动手前先看这里再触发 skill。
> v2 模型（2026-08-25）：服务器只做激活判码 + 发码，无 AI 代理、无网页版托管。

## 目标

一台 Linux VPS（腾讯云/阿里云轻量 2C2G 足够），跑一个纯 Flask + SQLite 服务。
依赖只有 flask，无出网调用，流量极小（每个买家一生只调一次激活接口）。

## 先选路线

| 路线 | 场景 | 入口 | 代价 |
|---|---|---|
| **A 境内裸 IP + 高位端口** | 想立刻上线、无域名 | `http://IP:7900` | 明文 HTTP |
| **B 域名 + HTTPS（Caddy）** | 有域名 | `https://lic.example.com` | 境内须先 ICP 备案 |

## 环境变量（/etc/biliparser-license.env，权限 600）

| 变量 | 作用 | 不设的后果 |
|---|---|---|
| `LICENSE_SIGN_KEY` | token 签名密钥（**必须与客户端包内 `_sign_key.txt` 一致**） | 用开发默认值——**等于没有签名**，生产必设 |
| `ADMIN_PASSWORD` | 管理后台登录密码 | 后台明确报错拒绝使用（防止裸奔） |
| `LICENSE_DB` | SQLite 路径（默认 `licenses.db`） | 用默认 |
| `RESTOCK_THRESHOLD` / `RESTOCK_TARGET` | 库存低于阈值补到目标（默认 10/50） | 用默认 |

## 密钥体系（泄露后果，务必清楚）

| 密钥 | 作用 | 泄露后果 |
|---|---|---|
| `LICENSE_SIGN_KEY` | HMAC 签名密钥，服务器和客户端各存一份 | 被提取后可伪造凭证（离线激活）；换它需发新版客户端 + 全员重新激活 |
| `ADMIN_PASSWORD` | 管理后台密码 | 后台失守（码被随意取走） |

好消息：v2 服务器**不再持有任何 AI key**（AI 是用户自己的），泄露面比 v1 小很多。

## 坑与风险

- **`LICENSE_SIGN_KEY` 与客户端不一致**：买家激活成功但客户端本地验签失败，
  卡在激活窗。发版前务必核对两边一致（打包脚本烧入的就是这个值）。
- **SQLite 多 worker**：gunicorn 建议 `-w 2` 即可；取码走 `BEGIN IMMEDIATE` +
  CAS，并发安全。
- **路线 A 明文 HTTP**：激活请求里的 SN/MAC 可被嗅探。激活是一次性事件，
  风险可接受；介意就走路线 B。
- **备份**：`licenses.db` 丢了所有码作废 → crontab 每日备份（见
  [admin-guide.md](admin-guide.md)）。

## 客户端指向服务器 + 烧密钥

打包时烧入（`packaging/build-macos.sh <server_url> <sign_key>`）：

- `_dist_server.txt`：激活服务器地址（如 `http://IP:7900`）
- `_sign_key.txt`：与服务器 `LICENSE_SIGN_KEY` 完全一致的签名密钥

开发调试可用环境变量覆盖：`BILIPARSER_LICENSE_SERVER` / `BILIPARSER_SIGN_KEY`。

## 日常运营

取码发货 / 退回 / 看库存 → 见 [admin-guide.md](admin-guide.md)。
