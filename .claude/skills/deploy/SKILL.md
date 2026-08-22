---
name: deploy
description: 部署 BiliParser 授权服务器（license-server/）到 Linux VPS——上传代码、装依赖、配密钥、systemd 常驻、Caddy HTTPS（可选）、网页版托管（可选）、安装包上架。当用户说「部署」「上线」「换服务器」「发布到 VPS」或 /deploy 时使用。
---

# 部署授权服务器（license-server/）

目标：一台 Linux VPS（Ubuntu 22.04/24.04，2C2G 够用），跑授权 + AI 代理服务。
本机用 ssh / rsync / scp 执行，不要求用户上服务器手敲。

决策与风险背景（路线怎么选、密钥泄露后果、坑）见
[docs/operations/deploy.md](docs/operations/deploy.md)。本 skill 只负责把步骤跑通。

## 0. 先向用户确认（没给的不要猜）

1. `SERVER` = `user@vps`（SSH 目标：登录用户 + IP/域名）——必须问，不能假设
2. 路线：`A`（境内裸 IP + 高位端口 7900）还是 `B`（域名 + HTTPS + 已备案）
3. 是否顺带部署网页版（hosted.py，:7842）和官网下载页

## 1. 上传代码（只需 license-server/ 目录）

```bash
rsync -av --exclude=.venv --exclude=__pycache__ --exclude='*.db' \
  license-server/ "$SERVER":/opt/biliparser-license/
# Windows 开发机没有 rsync 时用 scp：
# scp -r license-server "$SERVER":/opt/biliparser-license
```

## 2. 装依赖（服务器上）

```bash
ssh "$SERVER" 'cd /opt/biliparser-license && python3 -m venv .venv && .venv/bin/pip install flask httpx gunicorn'
```

## 3. 配密钥（环境变量，别进代码库）

本机生成两个强随机值：

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"   # 跑两次：SERVER_SECRET / ADMIN_KEY
```

写 `/etc/biliparser-license.env`（权限 600）。`GLM_API_KEY` 仅桌面版发行模式需要，
网页版走买家自己的 key 可留空：

```bash
ssh "$SERVER" 'sudo tee /etc/biliparser-license.env >/dev/null && sudo chmod 600 /etc/biliparser-license.env' <<'EOF'
SERVER_SECRET=<随机1>
ADMIN_KEY=<随机2>
GLM_API_KEY=<智谱key，可留空>
GLM_MODEL=glm-4-flash
LICENSE_DB=/opt/biliparser-license/licenses.db
EOF
```

## 4. systemd 常驻

绑定地址：路线 A 用 `-b 0.0.0.0:7900`；路线 B 改 `-b 127.0.0.1:7900`（HTTPS 由 Caddy 终结）。

```bash
ssh "$SERVER" 'sudo tee /etc/systemd/system/biliparser-license.service >/dev/null' <<'EOF'
[Unit]
Description=BiliParser license server
After=network.target

[Service]
WorkingDirectory=/opt/biliparser-license
EnvironmentFile=/etc/biliparser-license.env
ExecStart=/opt/biliparser-license/.venv/bin/gunicorn -w 2 -b 0.0.0.0:7900 app:create_app()
Restart=always

[Install]
WantedBy=multi-user.target
EOF

ssh "$SERVER" 'sudo systemctl enable --now biliparser-license'
```

验证存活（返回 403 = 服务活着，正常）：

```bash
ssh "$SERVER" 'curl -s -o /dev/null -w "%{http_code}\n" 127.0.0.1:7900/api/quota'
```

注意：SQLite + 多 worker 并发写没问题（写入量极小），但 gunicorn 用 `-w 2` 即可。

## 5a. 路线 A：境内裸 IP（无域名）

1. 让用户去云控制台安全组放行 `TCP 7900`（高位端口避开 80/443/8080）
2. 验证：`curl http://<IP>:7900/api/quota` 返回 403 JSON
3. 管理后台别明文过公网，让用户本地走 SSH 隧道：
   `ssh -L 7900:127.0.0.1:7900 user@<IP>` 后浏览器开 `http://127.0.0.1:7900/admin?key=<ADMIN_KEY>`

## 5b. 路线 B：域名 + HTTPS（Caddy）

先装 Caddy（官方源），然后只需两行：

```bash
ssh "$SERVER" 'sudo tee /etc/caddy/Caddyfile >/dev/null' <<EOF
<域名> {
    reverse_proxy 127.0.0.1:7900
}
EOF
ssh "$SERVER" 'sudo systemctl reload caddy'
```

完成即 `https://<域名>/admin?key=<ADMIN_KEY>`。

## 6.（可选）网页版托管 + 官网下载页

同一台机器再起 `biliparser-web`（:7842）。代码就是 `license-server/`（含 hosted.py），
另外要把 `src/biliparser` 整包拷到 `/opt/biliparser-license/biliparser/`
（hosted.py 复用其中的字幕/总结模块）：

```bash
tar --exclude=.venv --exclude=__pycache__ --exclude='*.db' \
    -czf deploy.tgz -C license-server . -C ../src biliparser
scp deploy.tgz "$SERVER":/tmp/ && ssh "$SERVER" 'sudo tar -xzf /tmp/deploy.tgz -C /opt/biliparser-license'
```

systemd 单元（密钥复用 `/etc/biliparser-license.env`）：

```bash
ssh "$SERVER" 'sudo tee /etc/systemd/system/biliparser-web.service >/dev/null' <<'EOF'
[Unit]
Description=BiliParser hosted web
After=network.target biliparser-license.service

[Service]
WorkingDirectory=/opt/biliparser-license
EnvironmentFile=/etc/biliparser-license.env
ExecStart=/opt/biliparser-license/.venv/bin/gunicorn -w 2 -b 0.0.0.0:7842 "hosted:create_app()"
Restart=always

[Install]
WantedBy=multi-user.target
EOF

ssh "$SERVER" 'sudo systemctl enable --now biliparser-web'
```

改过代码后两个服务都要重启（`biliparser-license` 和 `biliparser-web`）。
安全组再放行 `TCP 7842`，用户访问 `http://<IP>:7842` 输激活码登录。

## 7.（可选）官网与安装包分发

授权服务器本身就是官网：`/` 是下载页（`static-site/index.html`），`/download/<文件>`
下发安装包（`downloads/` 目录，不入 git）。

上架安装包：

```bash
# Windows 包只能由 CI 构建：打 tag 触发（.github/workflows/release.yml）
git tag v0.1.0 && git push origin v0.1.0     # CI 出 Release

# 把产物搬到服务器（mac 包本机也能出：bash packaging/build-macos.sh）
SERVER="$SERVER" bash packaging/sync-to-server.sh          # 从 Release 拉
SERVER="$SERVER" bash packaging/sync-to-server.sh local    # 或用本机 dist/
```

同步后用户可见：
- 官网：`https://<域名>/`（下载按钮自动高亮访客系统、显示最新版本号）
- 直链：`https://<域名>/download/BiliParser-macOS.dmg`、`.../BiliParser-Setup-Windows.exe`
- 路线 A（裸 IP）同理：`http://<IP>:7900/download/...`

## 收尾

部署完成后提醒用户：
1. 管理后台入口（路线 A 走 SSH 隧道，路线 B 走 https）
2. `licenses.db` 每日备份（crontab）
3. 客户端 server_url 填 `http://<IP>:7900` 或 `https://<域名>`（打包预置或用户在设置面板填）
