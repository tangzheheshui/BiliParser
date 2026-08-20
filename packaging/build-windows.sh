#!/usr/bin/env bash
# BiliParser Windows 打包：PyInstaller 出 onedir，再用 Inno Setup 打单文件安装包
# 用法：
#   bash packaging/build-windows.sh                              # 自用直连版（无激活）
#   bash packaging/build-windows.sh https://lic.example.com      # 发行版（烧入授权服务器，首启要求激活）
#
# 前置：
#   uv sync --group desktop                                      # pywebview + pyinstaller
#   安装 Inno Setup 6（https://jrsoftware.org/isinfo.php）        # 打 setup.exe 需要，未装则只出 onedir
set -euo pipefail
cd "$(dirname "$0")/.."

SERVER="${1:-}"
DIST_SERVER_FILE="packaging/_dist_server.txt"
rm -f "$DIST_SERVER_FILE"
if [ -n "$SERVER" ]; then
  printf '%s' "$SERVER" > "$DIST_SERVER_FILE"
  echo "[发行版] 烧入授权服务器: $SERVER"
fi

PY=".venv/Scripts/python.exe"
if [ ! -x "$PY" ]; then
  echo "[FAIL] 未找到 .venv/Scripts/python.exe，请先: uv sync --group desktop" >&2
  exit 1
fi

rm -rf build dist
"$PY" -m PyInstaller packaging/biliparser-win.spec --noconfirm --clean

ONEDIR="dist/BiliParser"
if [ ! -f "$ONEDIR/BiliParser.exe" ]; then
  echo "[FAIL] PyInstaller 打包失败（未找到 dist/BiliParser/BiliParser.exe）" >&2
  exit 1
fi
echo "[OK] onedir 产物: $ONEDIR"

# 可选：Inno Setup 打单文件安装包（需已安装 Inno Setup 6）
ISCC=""
for p in "/c/Program Files (x86)/Inno Setup 6/ISCC.exe" "/c/Program Files/Inno Setup 6/ISCC.exe"; do
  [ -f "$p" ] && ISCC="$p" && break
done
if [ -n "$ISCC" ]; then
  "$ISCC" packaging/biliparser.iss
  echo "[OK] 安装包: dist/BiliParser-Setup-*.exe"
else
  echo "[提示] 未找到 Inno Setup 6（ISCC.exe），已跳过安装包。"
  echo "       安装 https://jrsoftware.org/isinfo.php 后重跑本脚本即可产出 setup.exe"
fi

rm -f "$DIST_SERVER_FILE"
