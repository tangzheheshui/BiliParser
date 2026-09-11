---
name: deploy
description: 部署 BiliParser 授权服务器（license-server/，v2：激活码库存池 + 一次性激活）到 Linux VPS——上传代码、装依赖、配密钥、systemd 常驻、nginx + certbot 子域 HTTPS（可选）、安装包上架。当用户说「部署」「上线」「换服务器」「发布到 VPS」或 /deploy 时使用。
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
  license-server/ "$SERVER":/opt/BiliParser/license-server/
# Windows 开发机没有 rsync 时用 scp：
# scp -r license-server "$SERVER":/opt/BiliParser/license-server
```

注意：**别覆盖服务器上已有的 `licenses.db`**（那是卖出去的码）。
rsync 带 `--exclude='*.db'` 已防误删；若是换服务器迁移，单独把旧库拷过去。

**路径就认 `/opt/BiliParser/license-server/`（现行部署）。** 服务器上 `/opt/BiliParser`
是一个 git clone，部署方式是 rsync 直接盖进工作区，所以服务器上 `git status` 永远是
脏的——**这是预期的，别去 clean/checkout 它**，那会把线上代码回退成旧提交。

### 两个历史包袱，别踩

- `/opt/biliparser-license/`（注意没有大写和前一层）是 **v1 遗留目录**，2026-08-20/21
  那批文件，库是 v1 表结构（`licenses` / `prompts` / `usage` / `user_secrets` /
  `web_sessions`，**根本没有 `license_codes` 表**）。现行服务不读它。别往里部署，
  也别误把它当生产库——真正的码在 `/opt/BiliParser/license-server/licenses.db`。
  确认无用后可以整个删掉。
- `biliparser-web.service` 是 v1 的网页版托管，早已 `inactive` + `disabled`
  （`hosted.py` 在 v2 就删了）。**别 enable 它**，起来也是 ModuleNotFoundError。

## 2. 装依赖（服务器上）

```bash
ssh "$SERVER" 'cd /opt/BiliParser/license-server && python3 -m venv .venv && .venv/bin/pip install flask gunicorn'
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
LICENSE_DB=/opt/BiliParser/license-server/licenses.db
EOF
```

不设 `ADMIN_PASSWORD` 时后台明确报错拒用（防裸奔）；不设 `LICENSE_SIGN_KEY`
会用开发默认值——**等于没有签名**，生产必设。

## 4. systemd 常驻

绑定地址：路线 A 用 `-b 0.0.0.0:7900`；路线 B 改 `-b 127.0.0.1:7900`（HTTPS 由 nginx 终结）。

```bash
ssh "$SERVER" 'sudo tee /etc/systemd/system/biliparser-license.service >/dev/null' <<'EOF'
[Unit]
Description=BiliParser license server
After=network.target

[Service]
WorkingDirectory=/opt/BiliParser/license-server
EnvironmentFile=/etc/biliparser-license.env
ExecStart=/opt/BiliParser/license-server/.venv/bin/gunicorn -w 4 -t 300 -b 0.0.0.0:7900 "app:create_app()"
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

注意：SQLite + 多 worker 并发写没问题（写入量极小），`-w 4 -t 300` 是线上跑了很久的配置，
照抄即可。`-t`（worker 超时）对判码/发码这种快接口其实无关紧要，配大只是留余量，
v2 没有 AI 代理这类长请求，不用为它调。

## 5a. 路线 A：境内裸 IP（无域名）

1. 让用户去云控制台安全组放行 `TCP 7900`（高位端口避开 80/443/8080）
2. 验证：`curl http://<IP>:7900/download` 返回官网下载页（旧 `/site` 302 过来）
3. 管理后台别明文过公网，让用户本地走 SSH 隧道：
   `ssh -L 7900:127.0.0.1:7900 user@<IP>` 后浏览器开 `http://127.0.0.1:7900/admin`
   输 `ADMIN_PASSWORD` 登录

## 5b. 路线 B：子域 + HTTPS（nginx + certbot）

域名规范：**每个产品一个子域**（`xxx.tangzheheshui.cn`，根路径只放个人主页），
与同服的 MahjongHelper 同一套模式。四步：

```bash
# 1) DNS：控制台加 A 记录 biliparser → 服务器公网 IP（用户动手）
# 2) nginx 80 站点（ACME webroot + 反代）
ssh "$SERVER" 'sudo tee /etc/nginx/sites-available/biliparser >/dev/null' <<'EOF'
server {
    listen 80;
    server_name biliparser.tangzheheshui.cn;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { proxy_pass http://127.0.0.1:7900; proxy_set_header Host $host; }
}
EOF
ssh "$SERVER" 'sudo ln -sf /etc/nginx/sites-available/biliparser /etc/nginx/sites-enabled/ && sudo mkdir -p /var/www/certbot && sudo nginx -t && sudo systemctl reload nginx'
# 3) 签证书
ssh "$SERVER" 'sudo certbot certonly --webroot -w /var/www/certbot -d biliparser.tangzheheshui.cn'
# 4) 升级 443：server_name 不变，加 443 块（80 块改 301 跳 https），reload
```

443 块里证书路径 `/etc/letsencrypt/live/biliparser.tangzheheshui.cn/`，
`proxy_pass http://127.0.0.1:7900;`。**不设 URL_PREFIX**——子域挂根路径，
应用零改造。certbot 自动装续期定时任务，不用再管。

完成即 `https://biliparser.tangzheheshui.cn/admin`（输密码登录）。

## 6.（可选）官网与安装包分发

`/download` 是官网下载页（`static-site/`；旧 `/site` 302 过来），`/download/<文件>`
下发安装包（`downloads/` 目录，不入 git）。客户端底部「官网」链接指向服务根地址。

上架安装包：

```bash
# Windows 包只能由 CI 构建：打 tag 触发（.github/workflows/release.yml）
# tag 名要与 src/biliparser/__init__.py 的 __version__ 一致（pyproject.toml 也同步，
# tests/test_version_sync.py 兜底校验），否则包名/更新提示会对不上
git tag v0.2.6 && git push origin v0.2.6     # CI 出 Release

# 把产物搬到服务器（mac 包本机也能出：bash packaging/build-macos.sh <server_url> <sign_key>）
SERVER="$SERVER" bash packaging/sync-to-server.sh          # 从 Release 拉
SERVER="$SERVER" bash packaging/sync-to-server.sh local    # 或用本机 dist/
```

同步后用户可见：
- 官网：`https://biliparser.tangzheheshui.cn/download`（下载按钮自动高亮访客系统、显示最新版本号）
- 路线 A（裸 IP）同理：`http://<IP>:7900/download`

## 收尾

部署完成后提醒用户：
1. 管理后台入口（路线 A 走 SSH 隧道，路线 B 走 https），登录用 `ADMIN_PASSWORD`
2. `licenses.db` 每日备份（crontab）
3. 客户端打包时烧入同一 `LICENSE_SIGN_KEY` 与服务器地址
   （`bash packaging/build-macos.sh http://<IP>:7900 <sign_key>`）
4. 首启会自动生成 50 个激活码库存，后台「取一个激活码」即可发货
