---
name: deploy
description: 部署 BiliParser 授权服务器（license-server/，v2：激活码库存池 + 一次性激活）到 Linux VPS——上传代码、装依赖、配密钥、systemd 常驻、Caddy HTTPS（可选）、安装包上架。当用户说「部署」「上线」「换服务器」「发布到 VPS」或 /deploy 时使用。
---

# 部署授权服务器（license-server/，v2）

目标：一台 Linux VPS（Ubuntu 22.04/24.04，2C2G 够用），跑激活判码 + 发码服务。
v2 无 AI 代理、无网页版托管，依赖只有 flask。本机用 ssh / rsync / scp 执行，
不要求用户上服务器手敲。

决策与风险背景（路线怎么选、密钥泄露后果、坑）见
[docs/operations/deploy.md](docs/operations/deploy.md)。本 skill 只负责把步骤跑通。

## 0. 先向用户确认（没给的不要猜）

1. `SERVER` = `user@vps`（SSH 目标：登录用户 + IP/域名）——必须问，不能假设
2. 路线：`A`（境内裸 IP + 高位端口 7900）还是 `B`（域名 + HTTPS + 已备案）
3. `LICENSE_SIGN_KEY` 用哪个值——**必须与已发客户端包内 `_sign_key.txt` 一致**。
   首次部署可现场生成；已有客户端在流通时绝不能换（换了全员验签失败）。

## 1. 上传代码（只需 license-server/ 目录）

```bash
rsync -av --exclude=.venv --exclude=__pycache__ --exclude='*.db' --exclude=downloads \
  license-server/ "$SERVER":/opt/biliparser-license/
# Windows 开发机没有 rsync 时用 scp：
# scp -r license-server "$SERVER":/opt/biliparser-license
```

注意：**别覆盖服务器上已有的 `licenses.db`**（那是卖出去的码）。
rsync 带 `--exclude='*.db'` 已防误删；若是换服务器迁移，单独把旧库拷过去。

## 2. 装依赖（服务器上）

```bash
ssh "$SERVER" 'cd /opt/biliparser-license && python3 -m venv .venv && .venv/bin/pip install flask gunicorn'
```

## 3. 配密钥（环境变量，别进代码库）

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"   # 没有现成值时生成 SIGN_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(9))"  # ADMIN_PASSWORD（好记好输）
```

写 `/etc/biliparser-license.env`（权限 600）：

```bash
ssh "$SERVER" 'sudo tee /etc/biliparser-license.env >/dev/null && sudo chmod 600 /etc/biliparser-license.env' <<'EOF'
LICENSE_SIGN_KEY=<与客户端一致的签名密钥>
ADMIN_PASSWORD=<管理后台登录密码>
LICENSE_DB=/opt/biliparser-license/licenses.db
EOF
```

不设 `ADMIN_PASSWORD` 时后台明确报错拒用（防裸奔）；不设 `LICENSE_SIGN_KEY`
会用开发默认值——**等于没有签名**，生产必设。

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

验证存活（返回 200 + `{"code": 4, ...}` JSON = 服务活着，正常——业务码走 body）：

```bash
ssh "$SERVER" 'curl -s 127.0.0.1:7900/api/v1/license/activate -X POST -H "Content-Type: application/json" -d "{}"'
```

注意：SQLite + 多 worker 并发写没问题（写入量极小），gunicorn 用 `-w 2` 即可。

## 5a. 路线 A：境内裸 IP（无域名）

1. 让用户去云控制台安全组放行 `TCP 7900`（高位端口避开 80/443/8080）
2. 验证：`curl http://<IP>:7900/site` 返回官网页
3. 管理后台别明文过公网，让用户本地走 SSH 隧道：
   `ssh -L 7900:127.0.0.1:7900 user@<IP>` 后浏览器开 `http://127.0.0.1:7900/admin`
   输 `ADMIN_PASSWORD` 登录

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

完成即 `https://<域名>/admin`（输密码登录）。

## 6.（可选）官网与安装包分发

`/site` 是官网下载页（`static-site/`），`/download/<文件>` 下发安装包
（`downloads/` 目录，不入 git）。客户端底部「官网」链接指向这里。

上架安装包：

```bash
# Windows 包只能由 CI 构建：打 tag 触发（.github/workflows/release.yml）
git tag v0.2.0 && git push origin v0.2.0     # CI 出 Release

# 把产物搬到服务器（mac 包本机也能出：bash packaging/build-macos.sh <server_url> <sign_key>）
SERVER="$SERVER" bash packaging/sync-to-server.sh          # 从 Release 拉
SERVER="$SERVER" bash packaging/sync-to-server.sh local    # 或用本机 dist/
```

同步后用户可见：
- 官网：`https://<域名>/site`（下载按钮自动高亮访客系统、显示最新版本号）
- 直链：`https://<域名>/download/BiliParser-macOS.dmg`、`.../BiliParser-Setup-Windows.exe`
- 路线 A（裸 IP）同理：`http://<IP>:7900/site`、`http://<IP>:7900/download/...`

## 收尾

部署完成后提醒用户：
1. 管理后台入口（路线 A 走 SSH 隧道，路线 B 走 https），登录用 `ADMIN_PASSWORD`
2. `licenses.db` 每日备份（crontab）
3. 客户端打包时烧入同一 `LICENSE_SIGN_KEY` 与服务器地址
   （`bash packaging/build-macos.sh http://<IP>:7900 <sign_key>`）
4. 首启会自动生成 50 个激活码库存，后台「取一个激活码」即可发货
