# -*- mode: python ; coding: utf-8 -*-
"""BiliParser 打包配置（macOS / Windows 通用，按平台分支）。

产物：
  macOS   dist/BiliParser.app（onedir，build-macos.sh 再封 DMG）
  Windows dist/BiliParser/（onedir，CI 里再用 Inno Setup 封 setup.exe）
资源：src/biliparser/static/* → 产物内 biliparser/static/
      （web.py 用 Path(__file__).parent/'static' 定位，frozen 下同样成立）

发行版：构建时 packaging/_dist_server.txt 存在则烧入授权服务器地址
（用户拿到即要求激活）；不存在 = 自用直连版。
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
STATIC = ROOT / "src" / "biliparser" / "static"
DIST_SERVER_FILE = ROOT / "packaging" / "_dist_server.txt"
IS_MAC = sys.platform == "darwin"

datas = [(str(STATIC), "biliparser/static")]
if DIST_SERVER_FILE.exists():
    datas.append((str(DIST_SERVER_FILE), "biliparser"))

hidden = ["biliparser.desktop"]
if IS_MAC:
    hidden.append("webview.platforms.cocoa")          # pywebview macOS 后端
else:
    hidden += ["webview.platforms.edgechromium", "webview.platforms.winforms"]

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pip"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BiliParser",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # 桌面应用不开终端窗
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="BiliParser",
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="BiliParser.app",
        info_plist={
            "CFBundleDisplayName": "BiliParser",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "0.1.0",
            "LSMinimumSystemVersion": "12.0",
            # 允许 http 授权服务器（内网/初期）；正式上线换 https 后可删
            "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True},
        },
    )
