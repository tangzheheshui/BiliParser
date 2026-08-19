# -*- mode: python ; coding: utf-8 -*-
"""BiliParser macOS 打包配置。

产物：dist/BiliParser.app（onedir 结构，启动快、杀毒误报少）
资源：src/biliparser/static/* → 产物内 biliparser/static/
      （web.py 用 Path(__file__).parent/'static' 定位，frozen 下同样成立）

构建：packaging/build-macos.sh
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
STATIC = ROOT / "src" / "biliparser" / "static"
# 发行版：build-macos.sh 传入授权服务器地址时，会生成 packaging/_dist_server.txt，
# 烧进包内 → 用户拿到即要求激活；不带参数构建 = 自用直连版
DIST_SERVER_FILE = ROOT / "packaging" / "_dist_server.txt"
datas = [(str(STATIC), "biliparser/static")]
if DIST_SERVER_FILE.exists():
    datas.append((str(DIST_SERVER_FILE), "biliparser"))

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "webview.platforms.cocoa",       # pywebview macOS 后端
        "biliparser.desktop",
    ],
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
app = BUNDLE(
    coll,
    name="BiliParser.app",
    info_plist={
        "CFBundleDisplayName": "BiliParser",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "LSMinimumSystemVersion": "12.0",
        "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True},  # 允许 http 授权服务器（内网/初期）
    },
)
