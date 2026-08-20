#!/usr/bin/env bash
# 把安装包同步到你的官网服务器（授权服务器同机）。
#
# 用法：
#   SERVER=user@vps bash packaging/sync-to-server.sh            # 从 GitHub Release 拉
#   SERVER=user@vps bash packaging/sync-to-server.sh local      # 用本机 dist/ 的产物
#
# 说明：Windows 安装包只能由 CI 构建（本机是 macOS），CI 产物落在 GitHub
# Release；此脚本负责「搬到你的服务器」——用户侧一切都在你的域名下，
# 不接触 GitHub。macOS 包两种来源都行（本机 build-macos.sh 就能出）。
set -euo pipefail
cd "$(dirname "$0")/.."

SERVER="${SERVER:?需要 SERVER=user@vps}"
REMOTE_DIR="${REMOTE_DIR:-/opt/BiliParser/license-server/downloads}"
TAG="${TAG:-latest}"
SRC="${1:-release}"

mkdir -p license-server/downloads
cd license-server/downloads

if [ "$SRC" = "local" ]; then
  cp ../../dist/BiliParser.dmg BiliParser-macOS.dmg
  [ -f ../../dist/BiliParser-Setup-Windows.exe ] && cp ../../dist/BiliParser-Setup-Windows.exe . \
    || echo "[提示] 本机 dist/ 没有 Windows 安装包（需 CI 构建），仅同步 mac"
else
  # 规范式 /releases/download/<tag>/ 比标签式 /releases/<tag>/download/ 稳
  # （后者刚发布时常 404，CDN 传播延迟）。latest 时才用 latest/download。
  BASE="https://github.com/tangzheheshui/BiliParser/releases/download/$TAG"
  [ "$TAG" = "latest" ] && BASE="https://github.com/tangzheheshui/BiliParser/releases/latest/download"
  # 缺哪个资产不整体中断（CI 单平台失败时仍可同步另一个平台）
  curl -fsL --retry 3 --retry-delay 5 -o BiliParser-macOS.dmg          "$BASE/BiliParser-macOS.dmg" \
    || echo "[警告] mac 包下载失败（CI 是否成功？）"
  curl -fsL --retry 3 --retry-delay 5 -o BiliParser-Setup-Windows.exe  "$BASE/BiliParser-Setup-Windows.exe" \
    || echo "[警告] Windows 包下载失败（CI 是否成功？）"
fi

VERSION="${TAG#v}"
[ "$VERSION" = "latest" ] && VERSION="$(date +%Y-%m-%d)"
printf '{"version":"%s","updated":"%s"}\n' "$VERSION" "$(date +%Y-%m-%d)" > version.json

echo "[OK] 本地就绪：$(ls -lh | awk 'NR>1{print $9, $5}')"
rsync -av --progress . "$SERVER:$REMOTE_DIR/"
echo "[OK] 已同步到 $SERVER:$REMOTE_DIR"
echo "     官网下载地址：https://你的域名/download/BiliParser-macOS.dmg"
