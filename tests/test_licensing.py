"""客户端鉴权模块测试：MAC、混淆、激活返回码处理、本地 HMAC 验签（mock 网络）。

按《客户端需求文档.md》v2.1 第 7 节验收标准覆盖（联网部分 mock）。
"""

import json

import pytest

from biliparser import licensing

MAC = "AABBCCDDEEFF"          # 服务器规范化后返回（凭证里存的就是它）
MAC_RAW = "aa:bb:cc:dd:ee:ff"  # mac_address() 原值（上送服务器）
KEY = "test-sign-key-0123456789abcdef0123456789"
SN = "ABCD-1234-EFGH-5678"
ACTIVATED_AT = "2026-08-25 18:00:00"
_REAL_MAC = licensing.mac_address   # 捕获真实现（_isolate 会打桩 mac_address）


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(licensing, "LICENSE_PATH", tmp_path / "license.json")
    # 固定本机 MAC（测试机真实 MAC 不可知）：mac_address / _seed 全部走它
    monkeypatch.setattr(licensing, "mac_address", lambda: MAC_RAW)


def _token(sn=SN, mac=MAC, at=ACTIVATED_AT, key=KEY):
    import hashlib
    import hmac as _hmac
    return _hmac.new(key.encode(), f"{sn}|{mac}|{at}".encode(),
                     hashlib.sha256).hexdigest()


def _save_ok(**kw):
    sn, mac = kw.get("sn", SN), kw.get("mac", MAC)
    at = kw.get("activated_at", ACTIVATED_AT)
    token = kw["token"] if "token" in kw else _token(sn=sn, mac=mac, at=at)
    licensing._save(sn, mac, at, token)


# ---------- MAC 读取与规范化 ----------

def test_normalize_mac():
    assert licensing.normalize_mac("AA:BB:CC:DD:EE:FF") == "AABBCCDDEEFF"
    assert licensing.normalize_mac("aa-bb-cc-dd-ee-ff") == "AABBCCDDEEFF"
    assert licensing.normalize_mac("aabbccddeeff") == "AABBCCDDEEFF"
    assert licensing.normalize_mac("") == ""


def test_mac_address_darwin_first_en(monkeypatch):
    """macOS：取第一个 ether 非零的 en<N>（CR-03 固定策略）。"""
    class _R:
        def __init__(self, out): self.stdout = out

    def fake_run(cmd, **kw):
        if cmd == ["ifconfig", "-l"]:
            return _R("lo0 gif0 en0 en1 bridge0 utun0")
        if cmd == ["ifconfig", "en0"]:
            return _R("en0: flags=8863<UP>\n\tether aa:bb:cc:dd:ee:ff\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(licensing, "mac_address", _REAL_MAC)
    monkeypatch.setattr(licensing.subprocess, "run", fake_run)
    monkeypatch.setattr(licensing.sys, "platform", "darwin")
    assert licensing.mac_address() == "aa:bb:cc:dd:ee:ff"


def test_mac_address_skips_zero_en(monkeypatch):
    """en0 是全零（罕见）→ 取下一个 en1。"""
    class _R:
        def __init__(self, out): self.stdout = out

    def fake_run(cmd, **kw):
        if cmd == ["ifconfig", "-l"]:
            return _R("lo0 en0 en1")
        if cmd == ["ifconfig", "en0"]:
            return _R("en0: flags=8863\n\tether 00:00:00:00:00:00\n")
        if cmd == ["ifconfig", "en1"]:
            return _R("en1: flags=8863\n\tether 11:22:33:44:55:66\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(licensing, "mac_address", _REAL_MAC)
    monkeypatch.setattr(licensing.subprocess, "run", fake_run)
    monkeypatch.setattr(licensing.sys, "platform", "darwin")
    assert licensing.mac_address() == "11:22:33:44:55:66"


# ---------- 凭证存储（机器绑定混淆） ----------

def test_obfuscate_roundtrip_and_wrong_machine():
    text = "sn|mac|at|token"
    enc = licensing._obfuscate(text, MAC)
    assert licensing._deobfuscate(enc, MAC) == text
    # 换机器（MAC 不同）解出乱码
    assert licensing._deobfuscate(enc, "112233445566") != text


def test_save_and_load_credential():
    _save_ok()
    cred = licensing.load_credential()
    assert cred == {"sn": SN, "mac": MAC,
                    "activated_at": ACTIVATED_AT, "token": _token()}
    # 其他机器（seed 不同）读出来是乱码 → None
    assert licensing.load_credential(seed="112233445566") is None


def test_credential_missing_field_invalid():
    """凭证四字段缺任一 → None（文档 4.3）。"""
    _save_ok()
    path = licensing.LICENSE_PATH
    seed = licensing.normalize_mac(MAC)          # _save 用的 seed（大写规范化）
    raw = json.loads(path.read_text(encoding="utf-8"))
    payload = json.loads(licensing._deobfuscate(raw["data"], seed))
    del payload["activated_at"]
    raw["data"] = licensing._obfuscate(json.dumps(payload), seed)
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert licensing.load_credential() is None


def test_corrupt_file_returns_none():
    licensing.LICENSE_PATH.write_text("not json{{{", encoding="utf-8")
    assert licensing.load_credential() is None


# ---------- 激活（唯一联网交互） ----------

class _Resp:
    def __init__(self, code, data=None):
        self.status_code = 200
        self.content = b"1"
        self._d = {"code": code, "message": "", "data": data or {}}

    def json(self):
        return self._d


def test_activate_success_saves_credential(monkeypatch):
    captured = {}
    token = _token()

    def fake_post(url, json=None, timeout=None):
        captured.update(url=url, json=json)
        return _Resp(0, {"sn": SN, "mac": MAC,
                         "activated_at": ACTIVATED_AT, "token": token})

    monkeypatch.setattr(licensing.httpx, "post", fake_post)
    d = licensing.activate("http://s/", " " + SN.lower() + " ")
    assert d["token"] == token
    assert captured["url"] == "http://s/api/v1/license/activate"
    assert captured["json"] == {"mac": MAC_RAW, "sn": SN.lower()}
    # 凭证落盘，本地验签通过
    assert licensing.verify_local(key=KEY)["ok"] is True


def test_activate_error_codes(monkeypatch):
    """返回码 2/3/4/429/500 → 对应提示（客户端文档 3.3）。"""
    cases = {
        2: "激活码无效",
        3: "已被其他设备使用",
        4: "参数错误",
        429: "请求过于频繁",
        500: "网络异常",
    }
    for code, word in cases.items():
        monkeypatch.setattr(licensing.httpx, "post", lambda *a, **k: _Resp(code))
        with pytest.raises(licensing.LicensingError) as ei:
            licensing.activate("http://s", SN)
        assert word in str(ei.value), (code, str(ei.value))
        assert not licensing.LICENSE_PATH.exists()      # 失败不落盘


def test_activate_network_error(monkeypatch):
    def boom(*a, **k):
        raise licensing.httpx.ConnectError("no net")

    monkeypatch.setattr(licensing.httpx, "post", boom)
    with pytest.raises(licensing.LicensingError) as ei:
        licensing.activate("http://s", SN)
    assert "网络异常" in str(ei.value)
    # 已激活用户不受影响：本地凭证仍在
    _save_ok()
    try:
        licensing.activate("http://s", SN)
    except licensing.LicensingError:
        pass
    assert licensing.verify_local(key=KEY)["ok"] is True


# ---------- 启动本地校验（不联网） ----------

def test_verify_local_ok():
    _save_ok()
    assert licensing.verify_local(key=KEY) == {"ok": True, "reason": ""}


def test_verify_local_no_credential():
    assert licensing.verify_local(key=KEY)["ok"] is False
    assert licensing.verify_local(key=KEY)["reason"] == "未激活"


def test_verify_local_tampered_token():
    """篡改凭证（验收 #4：改 MAC/换 token 均校验失败）。"""
    # 直接写入一份「token 与字段对不上」的凭证
    licensing._save(SN, MAC, ACTIVATED_AT, "f" * 64)
    d = licensing.verify_local(key=KEY)
    assert d["ok"] is False and d["reason"] == "凭证校验失败"


def test_verify_local_wrong_machine():
    """凭证拷到另一台机器（验收 #5）：混淆 seed 不同 → 读不出来 → 走激活。"""
    _save_ok()
    d = licensing.verify_local(seed="112233445566", key=KEY)
    assert d["ok"] is False


def test_verify_local_mac_mismatch():
    """同 seed 解出来但当前机器 MAC 变了（如换网卡后 seed 恰好兼容的场景）：
    显式传 current_mac 模拟。"""
    _save_ok()
    d = licensing.verify_local(key=KEY, current_mac="112233445566")
    assert d["ok"] is False and d["reason"] == "设备不匹配"


def test_verify_local_wrong_key():
    """内置密钥与服务器不一致 → 验签失败（触发重新激活，不会误放行）。"""
    _save_ok()
    d = licensing.verify_local(key="another-key-entirely")
    assert d["ok"] is False and d["reason"] == "凭证校验失败"


def test_verify_local_never_touches_network(monkeypatch):
    """CR-01：本地校验全程不联网——把 httpx.post 炸掉照样通过。"""
    _save_ok()

    def boom(*a, **k):
        raise AssertionError("本地校验不应发起网络请求")

    monkeypatch.setattr(licensing.httpx, "post", boom)
    assert licensing.verify_local(key=KEY)["ok"] is True


# ---------- 启动联网核验 verify_remote（每次启动，远程吊销落点） ----------

def test_verify_remote_ok(monkeypatch):
    _save_ok()
    monkeypatch.setattr(licensing.httpx, "post", lambda *a, **k: _Resp(0))
    d = licensing.verify_remote("http://s/")
    assert d == {"checked": True, "ok": True, "reason": ""}
    assert licensing.LICENSE_PATH.exists()           # 凭证保留


def test_verify_remote_posts_sn_and_normalized_mac(monkeypatch):
    _save_ok()
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured.update(url=url, json=json)
        return _Resp(0)

    monkeypatch.setattr(licensing.httpx, "post", fake_post)
    licensing.verify_remote("http://s")
    assert captured["url"] == "http://s/api/v1/license/verify"
    assert captured["json"] == {"sn": SN, "mac": MAC}


def test_verify_remote_revoked_by_code3_clears_credential(monkeypatch):
    """后台解绑/换绑 → 服务器回 3 → 清凭证（下次启动走激活页）。"""
    _save_ok()
    monkeypatch.setattr(licensing.httpx, "post", lambda *a, **k: _Resp(3))
    d = licensing.verify_remote("http://s")
    assert d["checked"] is True and d["ok"] is False
    assert "其他设备" in d["reason"]
    assert not licensing.LICENSE_PATH.exists()       # 凭证被清


def test_verify_remote_revoked_by_code2(monkeypatch):
    _save_ok()
    monkeypatch.setattr(licensing.httpx, "post", lambda *a, **k: _Resp(2))
    d = licensing.verify_remote("http://s")
    assert d["ok"] is False and "失效" in d["reason"]
    assert not licensing.LICENSE_PATH.exists()


def test_verify_remote_offline_tolerant(monkeypatch):
    """服务器不可达 → 离线宽容：不清凭证、放行（离线也能用）。"""
    _save_ok()

    def boom(*a, **k):
        raise licensing.httpx.ConnectError("no net")

    monkeypatch.setattr(licensing.httpx, "post", boom)
    d = licensing.verify_remote("http://s")
    assert d == {"checked": False, "ok": True, "reason": "服务器不可达，离线放行"}
    assert licensing.LICENSE_PATH.exists()


def test_verify_remote_old_server_404_tolerant(monkeypatch):
    """老服务器没有 /verify（HTTP 404）→ 不吊销（升级期兼容）。"""
    _save_ok()

    class _404(_Resp):
        def __init__(self):
            super().__init__(0)
            self.status_code = 404

    monkeypatch.setattr(licensing.httpx, "post", lambda *a, **k: _404())
    d = licensing.verify_remote("http://s")
    assert d["checked"] is False and d["ok"] is True
    assert licensing.LICENSE_PATH.exists()


def test_verify_remote_rate_limited_not_revoked(monkeypatch):
    """429 是限流不是吊销 → 离线放行，凭证保留。"""
    _save_ok()
    monkeypatch.setattr(licensing.httpx, "post", lambda *a, **k: _Resp(429))
    d = licensing.verify_remote("http://s")
    assert d["checked"] is False and d["ok"] is True
    assert licensing.LICENSE_PATH.exists()


def test_verify_remote_no_credential(monkeypatch):
    monkeypatch.setattr(licensing.httpx, "post",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该发请求")))
    d = licensing.verify_remote("http://s")
    assert d == {"checked": False, "ok": False, "reason": "未激活"}
