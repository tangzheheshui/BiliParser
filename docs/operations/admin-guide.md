# 授权服务：产物与地址

服务器：`193.112.26.217:7900`（激活接口由客户端内置，无需手输）

| 产物 | 地址 |
|---|---|
| 管理后台（取码 / 退回 / 解绑） | http://193.112.26.217:7900/admin |
| 官网下载页（mac dmg / win exe） | http://193.112.26.217:7900/download |
| 安装包直链 | http://193.112.26.217:7900/download/BiliParser-macOS.dmg |

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
