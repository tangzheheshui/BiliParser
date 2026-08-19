"""PyInstaller 打包入口（打包时把 biliparser 包打进产物，此文件只做跳板）。"""

from biliparser.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
