# 授权服务器部署指南（license-server/）

目标：一台 Linux VPS（腾讯云/阿里云轻量 2C2G 足够，约 60 元/月），
跑授权 + AI 代理服务，HTTPS 访问。

## 0. 准备

- VPS（Ubuntu 22.04/24.04），安全组放行 80/443
- 域名一条 A 记录指到 VPS（如 `lic.example.com`）
- 智谱 API Key（服务器的 GLM 账户，用户 AI 调用从这里扣）

## 1. 上传代码

只需要 `license-server/` 目录：

```bash
rsync -av license-server/ user@vps:/opt/biliparser-license/
```

## 2. 环境与依赖

```bash
cd /opt/biliparser-license
python3 -m venv .venv
.venv/bin/pip install flask httpx gunicorn
```

## 3. 密钥（环境变量，别进代码库）

生成强随机值：

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"   # ×2，分别做 SERVER_SECRET 和 ADMIN_KEY
```

写 `/etc/biliparser-license.env`（权限 600）：

```bash
SERVER_SECRET=<长随机串>        # token 签名密钥，泄露=可伪造凭证，换它需全员重新激活
ADMIN_KEY=<长随机串>            # 管理后台密钥
GLM_API_KEY=<智谱key>
GLM_MODEL=glm-4-flash          # 服务端统一模型，成本可控；可换 glm-4.7 等
LICENSE_DB=/opt/biliparser-license/licenses.db
```

## 4. systemd 常驻

`/etc/systemd/system/biliparser-license.service`：

```ini
[Unit]
Description=BiliParser license server
After=network.target

[Service]
WorkingDirectory=/opt/biliparser-license
EnvironmentFile=/etc/biliparser-license.env
ExecStart=/opt/biliparser-license/.venv/bin/gunicorn -w 2 -b 127.0.0.1:7900 app:create_app\(\)
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now biliparser-license
curl 127.0.0.1:7900/api/quota   # 应返回 403 JSON（正常，说明服务活着）
```

注意：SQLite + 多 worker 并发写没问题（写入量极小），但建议 `-w 2` 即可。

## 5. HTTPS（Caddy，自动证书）

```bash
# 官方源安装 Caddy 后，/etc/caddy/Caddyfile 只需两行：
lic.example.com {
    reverse_proxy 127.0.0.1:7900
}
sudo systemctl reload caddy
```

完成后 `https://lic.example.com/admin?key=ADMIN_KEY` 即管理后台。

## 6. 日常运营

- **发码**：管理后台「生成」→ 复制发给买家（永久码留空有效期）
- **换机**：买家旧设备失联/卖二手 → 后台「解绑」→ 新设备可重新激活
  （解绑是滥用高发点，留意异常解绑次数）
- **退款/封禁**：后台「禁用」，该码所有设备立即失效（启动验证会拒绝）
- **用量**：列表页「今日用量/配额」实时可见；调配额在行内输入框
- **备份**：`licenses.db` 一个文件，crontab 每日拷走即可
  （丢库=发过的码全失效重发）

## 7. 客户端指向服务器

发行配置写入（打包时预置，或让用户在设置面板里填）：

```toml
[managed]
server_url = "https://lic.example.com"
```

或启动参数 `biliparser-desktop --server https://lic.example.com`。
