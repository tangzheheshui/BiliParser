# 需求：授权服务器（激活码库存池 + 一次性激活）

> 状态：已实现（2026-08-25，v2 模型）。**契约以 [服务器需求文档.md](服务器需求文档.md)
> （v2.2）为准**，本文是它在项目里的落地说明。客户端侧见
> [client.md](client.md)；部署见 [deploy.md](../operations/deploy.md)；
> 后台操作见 [admin-guide.md](../operations/admin-guide.md)。

## 定位（v2 重构后的边界）

服务器只做三件事：**存激活码、判一次激活、发码**。

- **一次性激活**：客户端输码时联网调一次 `POST /api/v1/license/activate`，
  绑定 MAC、下发 HMAC token，此后**永不再联系**。服务器宕机对已激活用户零影响。
- **不做**（相对 v1 砍掉的）：AI 代理（AI 改为用户自有 key 直连智谱/DeepSeek）、
  启动 verify、每日配额、试用登记、远程吊销、网页版托管（hosted.py 已删除）。
  这是有意取舍：售后只剩「发码」一件事，服务器可长期不管。

## 跨端决策（v2 修订记录）

| 决策点 | v1（旧） | v2（现） |
|---|---|---|
| AI 费用 | 服务器代理 + 按码配额 | **用户自有 key**（智谱/DeepSeek），卖家零成本零垫付 |
| 验证方式 | 每次启动联网 verify + 72h 宽限 | **一次性激活，本地 HMAC 验签**（离线永久可用） |
| 设备标识 | IOPlatformUUID 指纹 | **MAC 地址**（规范化：去分隔符大写 12 位 hex） |
| 试用 | 3 天试用登记 | 无试用 |
| 网页版 | hosted.py 多用户托管 | 删除 |
| 框架 | Flask | **仍 Flask**（对比过 FastAPI：async/自动 422 与「HTTP 恒 200 +
  业务码」约定相抵，规模下无收益，不换） |

防伪机制（与客户端文档一致）：token = HMAC-SHA256(LICENSE_SIGN_KEY,
`sn|mac|activated_at`)。安全边界如实：密钥随客户端分发、MAC 可伪造，
目标是防一码多机传播与随手改凭证，不防专业逆向。

## 服务器落地结构

```
license-server/（独立部署、独立 venv，仅 flask 依赖）
├── app.py           activate（限流+判活）/ admin 登录 / 取码 / 退回 / 列表 / 官网静态
├── db.py            SQLite：license_codes + activate_logs（仅此两张表）
├── static/admin.html  管理后台单页
├── static-site/     官网下载页（/site）
└── tests/test_api.py  22 用例 = 需求文档 §10 全部 16 场景 + 限流/分页/原子性
```

要点：

- **库存池**：启动空库自动生成 50 码；取码后未发货 < 10 自动补到 50
  （`RESTOCK_THRESHOLD` / `RESTOCK_TARGET` 可调）。
- **SN 格式**：`XXXX-XXXX-XXXX-XXXX`，字符集 `23456789ABCDEFGHJKMNPQRSTUVWXYZ`
  （去 0/O/1/I/L 防看错），secrets 生成 + 唯一性检查。
- **三态**：unshipped → shipped（取码）→ activated（终态，不可退回/重置）。
  未发货码可直接激活（跳过取码也放行，容错设计）。
- **取码 FIFO 原子**：`BEGIN IMMEDIATE` + CAS 更新（rowcount 校验），
  并发下不会两个卖家拿到同一个码。
- **HTTP 恒 200 + body 业务码**：0 成功 / 1 sn 空 / 2 无效码 / 3 设备不匹配 /
  4 参数错 / 401 未登录 / 429 限流 / 500 服务器错 / 501 库存空。
- **限流**：activate 按 IP 滑动窗口 60 次/分钟。
- **管理后台**：`ADMIN_PASSWORD` 登录（未设则明确报错），Bearer token 7 天。

## 本地联调（已验证的流程）

```bash
# 1. 授权服务器（开发默认签名密钥，别用于生产）
cd license-server && .venv/bin/python -m pytest   # 22 用例
.venv/bin/python app.py                            # :7900，默认 dev-sign-key-change-me
# 2. 管理后台取码：http://127.0.0.1:7900/admin → 登录 → 「取一个激活码」
# 3. 客户端指向它
BILIPARSER_LICENSE_SERVER=http://127.0.0.1:7900 uv run biliparse-web
BILIPARSER_SIGN_KEY=dev-sign-key-change-me uv run biliparse-web   # 客户端同密钥
# 4. 客户端输码激活 → 断网/杀服务器重启客户端 → 仍已激活（本地验签）
# 5. 打包：bash packaging/build-macos.sh http://127.0.0.1:7900 <sign_key>
```

## 官网与安装包分发

`/site` 是官网下载页（`static-site/`），`/download/<文件>` 下发安装包
（`downloads/` 目录，不入 git）。客户端底部「官网」链接固定指向
`http://<服务器>/site`。

## 已知边界 / 后续

- 远程吊销：2026-08-29 起客户端每次启动调 `/verify` 核验，后台解绑即踢老设备（0.2.4 及以前老客户端除外）；服务器不可达时离线宽容放行
- expires_at 字段表里保留但当前不用
- 发卡平台对接：管理后台取码后手动导入
