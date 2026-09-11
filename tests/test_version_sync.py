"""版本号单一来源：pyproject.toml 必须与 __init__.py 的 __version__ 一致。

真源是 `src/biliparser/__init__.py:__version__`（打包 spec、Info.plist、下载页
version.json 都由它生成）。pyproject 因 uv_build 不支持 dynamic version 只能
写死，所以在此兜底——bump 版本时只改一处会在这里红。
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _project_version() -> str:
    with open(ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


def _dunder_version() -> str:
    src = (ROOT / "src" / "biliparser" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', src).group(1)


def test_pyproject_matches_dunder_version():
    assert _project_version() == _dunder_version()
