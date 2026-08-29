# 授权服务：产物与地址

正式域名：`tangzheheshui.cn/biliparser`（域名根路径留给以后的个人主页；
nginx 80 反代 → 127.0.0.1:7900，应用按 `URL_PREFIX=/biliparser` 剥前缀，2026-08-29 起）。
旧地址 `193.112.26.217:7900` 仍直连可用——**7900 端口不能关**，
已发出的老客户端激活地址烧的是它。

| 产物 | 地址 |
|---|---|
| 官网（介绍 + 截图 + 下载） | http://tangzheheshui.cn/biliparser |
| 管理后台（取码 / 退回 / 解绑） | http://tangzheheshui.cn/biliparser/admin |
| 官网下载页（mac dmg / win exe） | http://tangzheheshui.cn/biliparser/download |

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
