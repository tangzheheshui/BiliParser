# 需求：桌面端 + 激活码发行体系

> 状态：已实现（2026-08）。原始需求来自用户提供的一份通用鉴权方案，
> 结合本项目现状做了 4 处修订后落地。

## 原始需求（用户方案摘要）

- 三部分：客户端（桌面软件）、授权服务器（Flask + SQLite）、管理后台
- `licenses` 表：code / device_fingerprint / activated_at / expires_at / is_active
- 三个 API：activate（绑定设备发 token）、verify（启动验证）、admin（生成/列表/禁用）
- 客户端流程：无凭证→激活窗→存凭证→进主界面；有凭证→启动验证→失效则回激活窗
- 防破解三层：指纹绑定一码一机、启动联网验证、关键逻辑放服务器
- 部署：轻量云服务器 + SQLite + HTTPS

## 拍板的决策

| 决策点 | 选择 |
|---|---|
| AI 费用 | **服务器代理**（用户开箱即用，GLM key 只在服务器） |
| 桌面栈 | **pywebview + PyInstaller**（复用现有 Python 后端 + HTML 界面） |
| 平台 | 先 macOS |
| 售卖 | **永久授权 + 一码一机**（换机走管理后台解绑） |

## 对原方案的 4 处修订（为什么）

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

## 落地结构

```
客户端（本仓库）
├── src/biliparser/licensing.py   指纹/激活/凭证（机器绑定混淆）/验证+宽限
├── src/biliparser/desktop.py     pywebview 壳（本地服务 + 原生窗口）
├── src/biliparser/static/activate.html  激活页
├── web.py：/api/license/*、/api/config/* 路由；index.html 激活门+设置面板
├── summarizer：cfg.managed_server 有值 → AI 走 /api/ai/chat 代理
└── packaging/：biliparser.spec + build-macos.sh → dist/BiliParser.app（26MB）

授权服务器（license-server/，独立部署、独立 venv）
├── app.py     activate/verify/ai/chat/quota + /admin 管理后台
├── db.py      SQLite：licenses + usage（按天计数）
└── tests/     12 个用例（激活/重绑/吊销/解绑/配额/转发）
```

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

- Windows 打包（MachineGuid 指纹 + CI）、正式签名/公证（当前未签名，
  用户首次打开需右键→打开）
- 发码/收款自动化（当前管理后台手动生成）
- 订阅制：表结构已含 expires_at，生成时可填天数，无独立 UI
