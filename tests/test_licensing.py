"""客户端授权模块测试：指纹、混淆、宽限逻辑（mock 网络）。"""

import json
import time
from datetime import datetime, timedelta

import pytest

from biliparser import licensing


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(licensing, "LICENSE_PATH", tmp_path / "license.json")


def test_fingerprint_stable_and_prefixed():
    # 不同平台前缀不同（WIN=注册表 MachineGuid / MAC=ioreg / FB=回退），但都应稳定
    fp1 = licensing.fingerprint()
    fp2 = licensing.fingerprint()
    assert fp1 == fp2
    assert fp1.startswith(("WIN-", "MAC-", "FB-"))


def test_obfuscate_roundtrip_and_wrong_fp():
    text = "token-abc-123"
    enc = licensing._obfuscate(text, "FP-1")
    assert licensing._deobfuscate(enc, "FP-1") == text
    # 换指纹解出乱码（拷贝凭证到别的机器无效）
    assert licensing._deobfuscate(enc, "FP-2") != text


def test_save_and_load_credential():
    licensing._save("http://s", "tok", "2026-08-22T00:00:00", "FP-1")
    cred = licensing.load_credential("FP-1")
    assert cred == {"server_url": "http://s", "token": "tok", "valid_until": "2026-08-22T00:00:00"}
    # 其他机器（指纹不同）读出来是乱码 → json 解析失败 → None
    assert licensing.load_credential("FP-2") is None


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.content = json.dumps(payload).encode()
        self._payload = payload

    def json(self):
        return self._payload


def test_activate_success(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured.update(url=url, json=json)
        return _Resp(200, {"token": "T1", "valid_until": "2026-08-22T00:00:00", "usage": {}})

    monkeypatch.setattr(licensing.httpx, "post", fake_post)
    d = licensing.activate("http://s/", " BP-CODE ", fp="FP-1")
    assert d["token"] == "T1"
    assert captured["url"] == "http://s/api/activate"
    assert captured["json"]["fingerprint"] == "FP-1"
    assert licensing.load_credential("FP-1")["token"] == "T1"


def test_activate_server_error(monkeypatch):
    monkeypatch.setattr(licensing.httpx, "post",
                        lambda *a, **k: _Resp(403, {"error": "激活码无效", "hint": "联系卖家"}))
    with pytest.raises(licensing.LicensingError) as ei:
        licensing.activate("http://s", "BAD", fp="FP-1")
    assert "无效" in str(ei.value) and ei.value.hint


def test_verify_not_activated():
    d = licensing.verify(fp="FP-1")
    assert not d["ok"] and d["reason"] == "未激活"


def test_verify_online_valid(monkeypatch):
    licensing._save("http://s", "T1", "2026-08-22T00:00:00", "FP-1")
    fresh = (datetime.now() + timedelta(hours=72)).isoformat()
    monkeypatch.setattr(licensing.httpx, "post",
                        lambda *a, **k: _Resp(200, {"valid": True, "valid_until": fresh,
                                                     "usage": {"today_used": 3, "daily_quota": 50}}))
    d = licensing.verify(fp="FP-1")
    assert d["ok"] and d["online"] and d["usage"]["today_used"] == 3
    # 宽限期已刷新落盘
    assert licensing.load_credential("FP-1")["valid_until"] == fresh


def test_verify_online_revoked(monkeypatch):
    licensing._save("http://s", "T1", "2099-01-01T00:00:00", "FP-1")
    monkeypatch.setattr(licensing.httpx, "post",
                        lambda *a, **k: _Resp(200, {"valid": False, "message": "激活码已被禁用"}))
    d = licensing.verify(fp="FP-1")
    assert not d["ok"] and "禁用" in d["reason"]


def test_verify_offline_grace(monkeypatch):
    future = (datetime.now() + timedelta(hours=24)).isoformat()
    licensing._save("http://s", "T1", future, "FP-1")

    def network_error(*a, **k):
        raise licensing.httpx.ConnectError("no net")

    monkeypatch.setattr(licensing.httpx, "post", network_error)
    d = licensing.verify(fp="FP-1")
    assert d["ok"] and not d["online"]


def test_verify_offline_expired(monkeypatch):
    past = (datetime.now() - timedelta(hours=1)).isoformat()
    licensing._save("http://s", "T1", past, "FP-1")

    def network_error(*a, **k):
        raise licensing.httpx.ConnectError("no net")

    monkeypatch.setattr(licensing.httpx, "post", network_error)
    d = licensing.verify(fp="FP-1")
    assert not d["ok"] and "72" in d["reason"]


def test_auth_header_requires_credential():
    with pytest.raises(licensing.LicensingError):
        licensing.auth_header(fp="FP-x")
    licensing._save("http://s", "T9", "2099-01-01T00:00:00", "FP-1")
    assert licensing.auth_header(fp="FP-1") == {"Authorization": "Bearer T9"}
