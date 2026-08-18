"""配置加载测试：Coding Plan 环境变量自动复用逻辑（不读真实配置文件）。"""

import pytest

from biliparser import config


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """指到临时路径，屏蔽本机 ~/.biliparser/config.toml 和相关环境变量。"""
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    for var in (
        "BILI_SESSDATA", "ZHIPUAI_API_KEY", "GLM_API_KEY",
        "BILIPARSER_MODEL", "BILIPARSER_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


def test_no_config_no_env():
    cfg = config.load_config()
    assert cfg.sessdata == "" and cfg.glm_api_key == ""
    assert cfg.glm_base_url == "https://open.bigmodel.cn/api/paas/v4/"


def test_coding_plan_autofill(monkeypatch):
    """智谱 Anthropic 端点凭证自动填空，模型去掉 [1M] 后缀。"""
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL", "glm-5.2[1M]")
    cfg = config.load_config()
    assert cfg.glm_api_key == "sk-test"
    assert cfg.glm_base_url == "https://open.bigmodel.cn/api/anthropic"
    assert cfg.glm_model == "glm-5.2"


def test_coding_plan_skipped_for_other_providers(monkeypatch):
    """非智谱的 Anthropic 兼容中转不复用（key 无效）。"""
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://relay.example.com/api")
    cfg = config.load_config()
    assert cfg.glm_api_key == ""
    assert cfg.glm_base_url == "https://open.bigmodel.cn/api/paas/v4/"


def test_explicit_env_wins_over_autofill(monkeypatch):
    """显式 GLM_API_KEY 优先，不触发 Coding Plan 复用。"""
    monkeypatch.setenv("GLM_API_KEY", "explicit")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-coding")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic")
    cfg = config.load_config()
    assert cfg.glm_api_key == "explicit"
    assert cfg.glm_base_url == "https://open.bigmodel.cn/api/paas/v4/"
