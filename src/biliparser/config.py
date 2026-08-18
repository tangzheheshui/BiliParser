"""配置加载：~/.biliparser/config.toml，环境变量优先覆盖。"""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path.home() / ".biliparser" / "config.toml"

CONFIG_TEMPLATE = """\
# BiliParser 配置文件

# B 站登录 Cookie（获取字幕必需）：
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

    # 环境变量优先
    cfg.sessdata = os.environ.get("BILI_SESSDATA", cfg.sessdata)
    env_key = os.environ.get("ZHIPUAI_API_KEY") or os.environ.get("GLM_API_KEY")
    if env_key:
        cfg.glm_api_key = env_key
    if os.environ.get("BILIPARSER_MODEL"):
        cfg.glm_model = os.environ["BILIPARSER_MODEL"]
    if os.environ.get("BILIPARSER_BASE_URL"):
        cfg.glm_base_url = os.environ["BILIPARSER_BASE_URL"]

    missing = [f for f in require if not getattr(cfg, f)]
    if missing:
        lines = ["、".join(_FIELD_HELP[f] for f in missing) + " 未配置。"]
        lines.append(f"请在配置文件中填写（支持环境变量覆盖）：{CONFIG_PATH}")
        lines.append("配置文件内容如下，创建后填入你的值即可：")
        lines.append(CONFIG_TEMPLATE)
        raise ConfigError("\n".join(lines))
    return cfg
