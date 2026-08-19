#!/usr/bin/env bash
# BiliParser macOS 打包：产出 dist/BiliParser.app
# 用法：
#   bash packaging/build-macos.sh                          # 自用直连版（无激活）
#   bash packaging/build-macos.sh https://lic.example.com  # 发行版（烧入授权服务器，首启要求激活）
set -euo pipefail
cd "$(dirname "$0")/.."

SERVER="${1:-}"
DIST_SERVER_FILE="packaging/_dist_server.txt"
rm -f "$DIST_SERVER_FILE"
if [ -n "$SERVER" ]; then
  printf '%s' "$SERVER" > "$DIST_SERVER_FILE"
  echo "[发行版] 烧入授权服务器: $SERVER"
fi

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  /opt/homebrew/bin/python3 -m venv .venv
fi
"$PY" -m pip install -q -e . pywebview pyinstaller 2>&1 | grep -v notice || true

rm -rf build dist
.venv/bin/pyinstaller packaging/biliparser.spec --noconfirm --clean

APP="dist/BiliParser.app"
if [ -d "$APP" ]; then
  echo "[OK] 打包完成: $APP ($(du -sh "$APP" | cut -f1))"
  echo "     双击运行, 或: open $APP"
  rm -f "$DIST_SERVER_FILE"
else
  echo "[FAIL] 打包失败" >&2
  exit 1
fi
