# 授权服务：产物与地址

正式域名：`biliparser.tangzheheshui.cn`（独立子域，与 MahjongHelper 同一套
nginx + certbot 模式；nginx 443 反代 → 127.0.0.1:7900，应用挂根路径）。
域名规范：**每个产品一个子域**（`xxx.tangzheheshui.cn`），根路径只放个人主页。
旧地址 `193.112.26.217:7900` 仍直连可用——**7900 端口不能关**，
已发出的老客户端激活地址烧的是它。

| 产物 | 地址 |
|---|---|
| 官网（介绍 + 截图 + 下载） | https://biliparser.tangzheheshui.cn/ |
| 管理后台（取码 / 退回 / 解绑） | https://biliparser.tangzheheshui.cn/admin |
| 官网下载页（mac dmg / win exe） | https://biliparser.tangzheheshui.cn/download |

## 迁移记录：子目录 → 独立子域（2026-09-07）

网站未发布、无存量流量，直接切换。VPS 上三步：

1. DNS：加 A 记录 `biliparser` → `193.112.26.217`
2. nginx：删掉根站点里 `/biliparser` 的 location，新增子域站点
   （80 + 443 反代 127.0.0.1:7900，ACME webroot 用 `/var/www/certbot`，
   与 mahjonghelper 站点同一套）；
   `sudo certbot certonly --webroot -w /var/www/certbot -d biliparser.tangzheheshui.cn`
3. `/etc/biliparser-license.env` 删掉 `URL_PREFIX=/biliparser`，
   `sudo systemctl restart biliparser-license`

## 后台三句话

1. 登录密码 = 部署时的 `ADMIN_PASSWORD`（`/etc/biliparser-license.env`）
2. 卖码：「取一个激活码」→ 复制发买家
3. 售后：搜索框粘码找记录——发错人「退回」，换电脑「解绑」

## 上架

新版安装包重命名后放到服务器 `/opt/BiliParser/license-server/downloads/`
（即 `/download/BiliParser-macOS.dmg`），下载页自动可用。

## 备份（重要）

`licenses.db` 一个文件就是全部激活码，丢了全作废：

```bash
0 3 * * * cp /opt/BiliParser/license-server/licenses.db /backup/licenses-$(date +\%Y\%m\%d).db
```
