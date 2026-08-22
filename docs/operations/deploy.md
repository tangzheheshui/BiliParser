# 授权服务器部署指南（license-server/）

> 执行步骤已做成 skill（`.claude/skills/deploy/`），说一句 `/deploy` 让 Claude 照着跑。
> 本页只记「决策 + 风险 + 为什么」，供人读；动手前先看这里再触发 skill。

## 目标

一台 Linux VPS（腾讯云/阿里云轻量 2C2G 足够，约 60 元/月），跑授权 + AI 代理服务。
独立于客户端，只部署 `license-server/` 目录。

## 先选路线

| 路线 | 场景 | 入口 | 代价 |
|---|---|---|---|
| **A 境内裸 IP + 高位端口** | 想立刻上线、无域名 | `http://IP:7900` | 明文 HTTP，靠高位端口避开备案拦截 |
| **B 域名 + HTTPS（Caddy）** | 有域名 | `https://lic.example.com` | 境内须先 ICP 备案（1~2 周） |

两条路线只差入口层（绑定地址 / 安全组 / 反代），其余步骤共用。

## 密钥体系（泄露后果，务必清楚）

| 密钥 | 作用 | 泄露后果 |
|---|---|---|
| `SERVER_SECRET` | token 签名密钥 | 可伪造任意用户凭证 → 换它需全员重新激活 |
| `ADMIN_KEY` | 管理后台密钥 | 后台失守（发码/禁用/配额全被控） |
| `GLM_API_KEY` | 智谱 key（仅桌面版发行模式） | 账户被刷钱 |

密钥写 `/etc/biliparser-license.env`（权限 600），永不进代码库。

## 坑与风险

- **SQLite 多 worker**：并发写没问题（写入量极小），但 gunicorn 建议 `-w 2` 即可。
- **路线 A 明文 HTTP 的暴露面**：激活码（一次性）、token、字幕正文可被链路嗅探；
  token 被截获最多蹭当日配额（默认 50 次/天，损失有上界）。
- **网页版 B 站风控**：网页版所有用户的 B 站请求都从服务器 IP 出去，量大触发风控
  （见 [server.md](../requirements/server.md)「网页版托管」）。
- **备份**：`licenses.db` 一个文件就是全部家当，丢了所有码作废 → crontab 每日备份。

## 日常运营

发码 / 换机 / 退款 / 配额 / 看用量 → 见 [admin-guide.md](admin-guide.md)。

## 客户端指向服务器

发行配置写入（打包时预置，或用户在设置面板填）：

```toml
[managed]
server_url = "http://服务器IP:7900"        # 路线 A
# server_url = "https://lic.example.com"  # 路线 B
```

或启动参数 `biliparser-desktop --server https://lic.example.com`。
