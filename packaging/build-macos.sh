#!/usr/bin/env bash
# BiliParser macOS 打包：产出 dist/BiliParser.app
# 用法：
#   bash packaging/build-macos.sh                                          # 自用直连版（无激活）
#   bash packaging/build-macos.sh <服务器URL> <签名密钥>                    # 发行版（烧入服务器+密钥，首启要求激活）
set -euo pipefail
cd "$(dirname "$0")/.."

SERVER="${1:-}"
SIGN_KEY="${2:-}"
DIST_SERVER_FILE="packaging/_dist_server.txt"
SIGN_KEY_FILE="packaging/_sign_key.txt"
rm -f "$DIST_SERVER_FILE" "$SIGN_KEY_FILE"
if [ -n "$SERVER" ]; then
  printf '%s' "$SERVER" > "$DIST_SERVER_FILE"
  echo "[发行版] 烧入授权服务器: $SERVER"
fi
if [ -n "$SIGN_KEY" ]; then
  printf '%s' "$SIGN_KEY" > "$SIGN_KEY_FILE"
  echo "[发行版] 烧入签名密钥: ${SIGN_KEY:0:4}****（必须与服务器 LICENSE_SIGN_KEY 一致）"
fi

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  /opt/homebrew/bin/python3 -m venv .venv
fi
"$PY" -m pip install -q -e . pywebview pyinstaller 2>&1 | grep -v notice || true

rm -rf build dist
.venv/bin/pyinstaller packaging/biliparser.spec --noconfirm --clean

APP="dist/BiliParser.app"
if [ ! -d "$APP" ]; then
  echo "[FAIL] 打包失败" >&2
  exit 1
fi
echo "[OK] 打包完成: $APP ($(du -sh "$APP" | cut -f1))"

# 烧入文件双保险：PyInstaller 布局随版本漂移（Resources/ 与 Frameworks/ 都出过，
# 曾因此运行时读不到 → 正式版被当直连版「免激活」）。两处各放一份，
# 运行时 licensing.bundled_text() 在哪个都读得到。
for d in "$APP/Contents/Frameworks/biliparser" "$APP/Contents/Resources/biliparser"; do
  mkdir -p "$d"
  [ -f "$DIST_SERVER_FILE" ] && cp "$DIST_SERVER_FILE" "$d/"
  [ -f "$SIGN_KEY_FILE" ] && cp "$SIGN_KEY_FILE" "$d/"
done

# 封装 DMG（拖入 Applications 的标准 mac 分发格式；hdiutil 系统自带）
DMG="dist/BiliParser.dmg"
rm -f "$DMG"
STAGING="dist/dmg-staging"
rm -rf "$STAGING" && mkdir -p "$STAGING"
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
hdiutil create -volname "BiliParser" -srcfolder "$STAGING" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGING"
echo "[OK] DMG 完成: $DMG ($(du -sh "$DMG" | cut -f1))"
echo "     双击安装（拖入 Applications）, 或: open $DMG"

# 版本清单：随 dmg 一起上架到服务器 downloads/，客户端启动比对提示更新
VERSION=$("$PY" -c 'import re;print(re.search(r"__version__ = \"([^\"]+)\"", open("src/biliparser/__init__.py").read()).group(1))')
printf '{"version":"%s","updated":"%s"}\n' "$VERSION" "$(date +%Y-%m-%d)" > dist/version.json
echo "[OK] 版本清单: $(cat dist/version.json)"
echo "[MD5] $(md5 -q "$DMG")  （上传后必须核对服务器一致）"
rm -f "$DIST_SERVER_FILE" "$SIGN_KEY_FILE"
