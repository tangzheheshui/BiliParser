# -*- mode: python ; coding: utf-8 -*-
"""BiliParser Windows 打包配置（PyInstaller）。

产物：dist/BiliParser/（onedir 结构，BiliParser.exe + 依赖，启动快、杀毒误报少）
资源：src/biliparser/static/* → 产物内 biliparser/static/
      （web.py 用 Path(__file__).parent/'static' 定位，frozen 下同样成立）
图标：packaging/biliparser.ico 存在则作为 exe 图标，否则用默认

构建：packaging/build-windows.sh（PyInstaller 后可选 Inno Setup 打 setup.exe）
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
STATIC = ROOT / "src" / "biliparser" / "static"
# 发行版：build-windows.sh 传入授权服务器地址时，会生成 packaging/_dist_server.txt，
# 烧进包内 → 用户拿到即要求激活；不带参数构建 = 自用直连版
DIST_SERVER_FILE = ROOT / "packaging" / "_dist_server.txt"
ICON_FILE = ROOT / "packaging" / "biliparser.ico"

datas = [(str(STATIC), "biliparser/static")]
if DIST_SERVER_FILE.exists():
    datas.append((str(DIST_SERVER_FILE), "biliparser"))

exe_kwargs = {}
if ICON_FILE.exists():
    exe_kwargs["icon"] = str(ICON_FILE)

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "webview.platforms.edgechromium",   # pywebview Windows 后端（WebView2）
        "webview.platforms.winforms",
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
    **exe_kwargs,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="BiliParser",
)
