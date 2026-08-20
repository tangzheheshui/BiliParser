"""配置加载：~/.biliparser/config.toml，环境变量优先覆盖。"""

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path.home() / ".biliparser" / "config.toml"

CONFIG_TEMPLATE = """\
# BiliParser 配置文件

# B 站登录 Cookie（可选，填了能解锁 AI 字幕；不填走 CC 字幕/官方总结/降级）：
# 电脑浏览器登录 bilibili.com → F12 开发者工具 → 应用 → Cookie
#   → 找到 SESSDATA，复制它的值（不含引号）
sessdata = ""

[glm]
# 智谱 API Key：https://bigmodel.cn 注册 → 控制台 → API Keys
api_key = ""
# 模型可换 glm-5.2 等，见 https://bigmodel.cn/pricing
model = "glm-4.7"
base_url = "https://open.bigmodel.cn/api/paas/v4/"
"""

_FIELD_HELP = {
    "sessdata": "B 站 SESSDATA（浏览器 F12 → 应用 → Cookie）",
    "glm_api_key": "智谱 GLM API Key（https://bigmodel.cn 控制台）",
}


class ConfigError(Exception):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


@dataclass
class Config:
    sessdata: str = ""
    glm_api_key: str = ""
    glm_model: str = "glm-4.7"
    glm_base_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    glm_provider: str = ""           # 设置面板状态用：zhipu/deepseek/server
    # 发行版模式：授权服务器地址。设置后 AI 调用走服务器代理（需已激活），
    # 留空 = 直连模式（自用，本地配 GLM key）
    managed_server: str = ""


def load_config(require: tuple[str, ...] = ()) -> Config:
    """读配置文件并用环境变量覆盖；require 里列出的字段缺失时报错。

    环境变量：BILI_SESSDATA / ZHIPUAI_API_KEY（或 GLM_API_KEY）/
    BILIPARSER_MODEL / BILIPARSER_BASE_URL
    """
    cfg = Config()
    if CONFIG_PATH.exists():
        try:
            raw = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(
                f"配置文件格式错误：{CONFIG_PATH}\n{e}", hint="请检查 TOML 语法（字符串要双引号）"
            ) from e
        cfg.sessdata = str(raw.get("sessdata", "") or "")
        glm = raw.get("glm", {})
        if not isinstance(glm, dict):
            raise ConfigError(f"配置文件格式错误：[glm] 应为表（key = \"value\" 形式）")
        cfg.glm_api_key = str(glm.get("api_key", "") or "")
        if glm.get("model"):
            cfg.glm_model = str(glm["model"])
        if glm.get("base_url"):
            cfg.glm_base_url = str(glm["base_url"])
        cfg.glm_provider = str(glm.get("provider", "") or "")
        managed = raw.get("managed", {})
        if isinstance(managed, dict):
            cfg.managed_server = str(managed.get("server_url", "") or "")

    # 环境变量优先
    cfg.sessdata = os.environ.get("BILI_SESSDATA", cfg.sessdata)
    env_key = os.environ.get("ZHIPUAI_API_KEY") or os.environ.get("GLM_API_KEY")
    if env_key:
        cfg.glm_api_key = env_key
    if os.environ.get("BILIPARSER_MODEL"):
        cfg.glm_model = os.environ["BILIPARSER_MODEL"]
    if os.environ.get("BILIPARSER_BASE_URL"):
        cfg.glm_base_url = os.environ["BILIPARSER_BASE_URL"]
    if os.environ.get("BILIPARSER_LICENSE_SERVER"):
        cfg.managed_server = os.environ["BILIPARSER_LICENSE_SERVER"]
    # 发行版烧入的授权服务器是权威值（打包时写死，见 packaging/build-macos.sh）：
    # 高于配置文件里的 [managed]——用户/测试残留的旧地址不能劫持正式版。
    # 开发者想临时改用别的服务器，用上面的环境变量。
    bundled = Path(__file__).parent / "_dist_server.txt"
    if bundled.exists():
        server = bundled.read_text(encoding="utf-8").strip()
        if server:
            cfg.managed_server = server

    # 未配置 GLM key 时，自动复用环境里的智谱 Coding Plan 凭证
    # （即 Claude Code 经 ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN 走的那套，
    # 与 paas/v4 的 API 资源包是两个计费池）。只填空位，不覆盖上面的显式配置。
    if not cfg.glm_api_key:
        token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
        base = os.environ.get("ANTHROPIC_BASE_URL", "")
        if token and "bigmodel.cn" in base:
            cfg.glm_api_key = token
            cfg.glm_base_url = base.rstrip("/")
            if os.environ.get("ANTHROPIC_MODEL"):
                # 形如 glm-5.2[1M] 的上下文长度后缀去掉
                cfg.glm_model = re.sub(r"\[[^\]]*\]$", "", os.environ["ANTHROPIC_MODEL"])

    missing = [f for f in require if not getattr(cfg, f)]
    if missing:
        lines = ["、".join(_FIELD_HELP[f] for f in missing) + " 未配置。"]
        lines.append(f"请在配置文件中填写（支持环境变量覆盖）：{CONFIG_PATH}")
        lines.append("配置文件内容如下，创建后填入你的值即可：")
        lines.append(CONFIG_TEMPLATE)
        raise ConfigError("\n".join(lines))
    return cfg


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def update_config(values: dict) -> None:
    """把 {'sessdata': …, 'managed.server_url': …} 写回配置文件（点号表示子表）。

    注意：会重写整个文件（我们的 schema 只有「顶层标量 + 一层子表」），
    手写注释会丢。字段值为 None 的跳过不动。
    """
    raw: dict = {}
    if CONFIG_PATH.exists():
        try:
            raw = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            raw = {}
    for key, val in values.items():
        if val is None:
            continue
        if "." in key:
            table, field = key.split(".", 1)
            raw.setdefault(table, {})[field] = val
        else:
            raw[key] = val

    lines = ["# BiliParser 配置文件（由应用设置面板维护，注释会被覆盖）"]
    for key, val in raw.items():
        if not isinstance(val, dict):
            lines.append(f"{key} = {_toml_value(val)}")
    for name, table in raw.items():
        if not isinstance(table, dict):
            continue
        lines.append("")
        lines.append(f"[{name}]")
        for key, val in table.items():
            if isinstance(val, dict):
                continue  # 只支持一层
            lines.append(f"{key} = {_toml_value(val)}")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
