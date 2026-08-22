# 需求：授权服务器 + 网页版托管

> 状态：已实现（2026-08）。独立部署、独立 venv 的 Flask 服务（`license-server/`），
> 承载：授权（activate/verify）、AI 代理、管理后台、网页版托管、官网下载页。
> 客户端侧（激活/离线宽限/桌面壳）见 [client.md](docs/requirements/client.md)；
> 部署见 [deploy.md](docs/operations/deploy.md)；后台操作见
> [admin-guide.md](docs/operations/admin-guide.md)。

## 定位

授权服务器是「售卖版」的核心：GLM key 只在服务器、按激活码做每日配额、随时可
吊销。客户端只带 token 调它，不碰钱。

## 跨端决策（拍板与修订）

> 这是整套授权体系「为什么长这样」的决策记录，客户端和服务器都受它约束。

### 原始需求（用户方案摘要）

- 三部分：客户端（桌面软件）、授权服务器（Flask + SQLite）、管理后台
- `licenses` 表：code / device_fingerprint / activated_at / expires_at / is_active
- 三个 API：activate（绑定设备发 token）、verify（启动验证）、admin（生成/列表/禁用）
- 客户端流程：无凭证→激活窗→存凭证→进主界面；有凭证→启动验证→失效则回激活窗
- 防破解三层：指纹绑定一码一机、启动联网验证、关键逻辑放服务器
- 部署：轻量云服务器 + SQLite + HTTPS

### 拍板的决策

| 决策点 | 选择 |
|---|---|
| AI 费用 | **服务器代理**（用户开箱即用，GLM key 只在服务器） |
| 桌面栈 | **pywebview + PyInstaller**（复用现有 Python 后端 + HTML 界面） |
| 平台 | 先 macOS |
| 售卖 | **永久授权 + 一码一机**（换机走管理后台解绑） |

### 对原方案的 4 处修订（为什么）

1. **B 站请求不走服务器**：所有用户从同一 IP 拉 B 站必触发风控。只有
   GLM AI 调用走服务器；字幕请求留在用户本机（用户自己的 SESSDATA）。
2. **AI 代理加配额**：服务器付钱模式下必须有 per-license 每日配额
   （默认 50 次/天，管理后台可调），否则一个码能刷爆账户。
3. **72h 离线宽限**：原方案「每次启动必须联网」体验太差。服务器验证
   成功时下发 valid_until（+72h），断网宽限期内可正常使用；AI 调用
   仍必须在线（真正的付费价值在服务端把着）。
4. **指纹用 IOPlatformUUID**：MAC 地址会变，不能用；macOS 用
   `ioreg` 读 IOPlatformUUID 哈希（重装系统才变）。

安全边界（如实）：Python 客户端可被逆向，混淆只防「拷贝 license.json
到别的机器」；真正的防线 = GLM key 永不落客户端 + 服务端吊销 + 配额。

## 服务器落地结构

```
license-server/（独立部署、独立 venv）
├── app.py           activate/verify/ai/chat/quota + /admin 管理后台
├── db.py            SQLite：licenses + usage（按天计数）
├── hosted.py        网页版托管（复用 src/biliparser 的字幕/总结模块）
├── static-site/     官网下载页
└── tests/           激活/重绑/吊销/解绑/配额/转发/托管
```

## 管理后台

卖家日常发码/售后/看数据的界面，全程只认激活码、无用户注册。操作详见
[admin-guide.md](docs/operations/admin-guide.md)，这里只记要点：

- 生成 / 批量导出（对接发卡平台）/ 禁用 / 启用 / 解绑 / 调配额
- 列表列：状态（未激活/已使用·网页/已激活·桌面/已禁用）、绑定设备、首次使用、
  最近活跃、今日用量/配额、解绑次数
- `licenses.db` 一个文件就是全部家当，丢了所有码作废 → 每日备份

## 网页版托管（hosted.py，多用户）

把 Web 工作台搬到服务器上对外发布。**一码通用**——同一个激活码既能激活桌面版
（占设备位），也能登录网页版（不占设备位）。不需要注册系统，激活码即账号。

- 登录：前端探测 `/api/status` 返回 401 → 弹登录浮层 → `POST /api/login {code}`
  → 授权服务器 `/api/web/login` 发 WEB 指纹 token → Flask 签名 cookie（30 天）
- 会话限制：一码最多 2 个同时在线网页会话，第 3 个登录挤掉最老（防共享）
- SESSDATA：用户在设置面板自己填，按激活码加密落库（`user_secrets` 表，
  SERVER_SECRET 派生密钥流异或），换浏览器不用重填
- 自定义模板：`prompts` 表按激活码隔离
- AI：买家在设置面板自配 API key（智谱 GLM / DeepSeek 二选一，均 OpenAI
  兼容），加密落库；总结时服务器代调买家选的提供商（`_Cfg` 走 OpenAI 路径，
  不设 managed_server）。**费用买家自付，卖家不垫钱、不限配额**
- 风险边界：网页版所有用户的 B 站请求都从服务器 IP 出去（桌面版在用户本机）。
  几十个用户可控；量大触发 B 站风控，到时需多出口 IP 分摊

## 官网与安装包分发

授权服务器本身就是官网：`/` 是下载页（`static-site/index.html`），
`/download/<文件>` 下发安装包（`downloads/` 目录，不入 git）。
Windows 包由 CI 打 tag 构建，`packaging/sync-to-server.sh` 上架。详见
[deploy.md](docs/operations/deploy.md) 第 10 节。

## 本地联调（已验证的流程）

```bash
# 1. 授权服务器（开发模式，密钥用默认值，别用于生产）
cd license-server && .venv/bin/python app.py        # :7900
# 2. 管理后台生成激活码：http://127.0.0.1:7900/admin?key=dev-admin
# 3. 工作台以发行模式启动
BILIPARSER_LICENSE_SERVER=http://127.0.0.1:7900 uv run biliparse-web
#    或桌面版：uv run biliparser-desktop --server http://127.0.0.1:7900
# 4. 浏览器被重定向到激活页 → 输码 → 主界面 → 总结走代理（服务器计量）
# 5. 打包：bash packaging/build-macos.sh → dist/BiliParser.app
```

## 已知边界 / 后续

- 发码/收款自动化（当前管理后台手动生成）
- 订阅制：表结构已含 expires_at，生成时可填天数，无独立 UI
