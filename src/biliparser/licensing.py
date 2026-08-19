"""客户端授权：设备指纹、激活、凭证存取（机器绑定混淆）、验证 + 72h 离线宽限。

安全边界（如实）：本文件落在客户端，可被逆向。混淆只防「拷贝 license.json
到别的机器」这种顺手盗用；真正的防线在服务器——AI 调用必须持有效 token
实时过服务器校验，吊销/配额都在服务端生效。
"""

import hashlib
import json
import subprocess
import time
from pathlib import Path

import httpx

LICENSE_PATH = Path.home() / ".biliparser" / "license.json"
GRACE_SECONDS = 72 * 3600  # 服务器下发的 valid_until 之后再宽限这么久


class LicensingError(Exception):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


# ---------------- 设备指纹 ----------------

def fingerprint() -> str:
    """稳定设备指纹：macOS 用 IOPlatformUUID（重装系统才变），其他平台
    回退到 hostname+家目录哈希（Windows 打包时换注册表 MachineGuid）。"""
    try:
        out = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True, text=True, timeout=5,
        )
        for line in out.stdout.splitlines():
            if "IOPlatformUUID" in line:
                uuid = line.split("=")[-1].strip().strip('"')
                return "MAC-" + hashlib.sha256(uuid.encode()).hexdigest()[:24]
    except (OSError, subprocess.SubprocessError):
        pass
    raw = f"{Path.home()}"
    return "FB-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


# ---------------- 凭证存储（机器绑定混淆） ----------------

def _keystream(seed: str, n: int) -> bytes:
    """指纹派生密钥流：sha256 计数器模式。换台机器密钥不同，拷文件无效。"""
    out = b""
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(f"{seed}:{counter}".encode()).digest()
        counter += 1
    return out[:n]


def _obfuscate(text: str, fp: str) -> str:
    data = text.encode()
    key = _keystream(fp, len(data))
    return bytes(a ^ b for a, b in zip(data, key)).hex()


def _deobfuscate(hex_text: str, fp: str) -> str:
    data = bytes.fromhex(hex_text)
    key = _keystream(fp, len(data))
    return bytes(a ^ b for a, b in zip(data, key)).decode("utf-8", errors="replace")


def _save(server_url: str, token: str, valid_until: str, fp: str) -> None:
    payload = json.dumps({"server_url": server_url, "token": token,
                          "valid_until": valid_until}, ensure_ascii=False)
    LICENSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LICENSE_PATH.write_text(
        json.dumps({"v": 1, "data": _obfuscate(payload, fp)}), encoding="utf-8"
    )


def load_credential(fp: str | None = None) -> dict | None:
    """读取本地凭证；文件不存在/损坏/指纹不符（拷来的）返回 None。"""
    if not LICENSE_PATH.exists():
        return None
    try:
        raw = json.loads(LICENSE_PATH.read_text(encoding="utf-8"))
        payload = _deobfuscate(raw["data"], fp or fingerprint())
        cred = json.loads(payload)
        return {k: cred[k] for k in ("server_url", "token", "valid_until")}
    except (KeyError, ValueError, OSError):
        return None


def clear_credential() -> None:
    LICENSE_PATH.unlink(missing_ok=True)


# ---------------- 激活与验证 ----------------

def activate(server_url: str, code: str, fp: str | None = None) -> dict:
    """输码激活：服务器绑定设备并发 token，成功后凭证落盘。"""
    fp = fp or fingerprint()
    try:
        resp = httpx.post(
            server_url.rstrip("/") + "/api/activate",
            json={"code": code.strip(), "fingerprint": fp}, timeout=15,
        )
    except httpx.HTTPError as e:
        raise LicensingError(
            f"连不上授权服务器（{e.__class__.__name__}）", hint="检查网络后重试"
        ) from e
    data = resp.json() if resp.content else {}
    if resp.status_code != 200:
        raise LicensingError(
            data.get("error", f"激活失败（HTTP {resp.status_code}）"),
            hint=data.get("hint"),
        )
    _save(server_url, data["token"], data["valid_until"], fp)
    return data


def verify(server_url: str | None = None, fp: str | None = None,
           now: float | None = None) -> dict:
    """启动验证。返回 {ok, online, reason, usage, valid_until}。

    逻辑：本地无凭证 → 未激活；在线验证成功 → 刷新宽限期；服务器明确说
    无效 → 拒绝（吊销/解绑/过期）；网络不通 → 72h 宽限内放行（离线模式）。
    """
    now = now if now is not None else time.time()
    fp = fp or fingerprint()
    cred = load_credential(fp)
    if not cred:
        return {"ok": False, "online": False, "reason": "未激活"}

    base = (server_url or cred["server_url"]).rstrip("/")
    try:
        resp = httpx.post(base + "/api/verify", json={"token": cred["token"]}, timeout=8)
        data = resp.json() if resp.content else {}
        if resp.status_code == 200 and data.get("valid"):
            _save(cred["server_url"], cred["token"], data["valid_until"], fp)
            return {"ok": True, "online": True, "usage": data.get("usage"),
                    "valid_until": data["valid_until"]}
        reason = data.get("message", "凭证无效")
        return {"ok": False, "online": True, "reason": reason}
    except httpx.HTTPError:
        pass  # 离线：走宽限判定

    try:
        from datetime import datetime
        valid_until = datetime.fromisoformat(cred["valid_until"]).timestamp()
    except (ValueError, KeyError):
        valid_until = 0
    if now < valid_until + 0:  # 服务器已把 72h 算进 valid_until
        return {"ok": True, "online": False, "reason": "离线宽限期内", "valid_until": cred["valid_until"]}
    return {"ok": False, "online": False, "reason": "离线超过 72 小时，请联网后重启验证"}


def auth_header(fp: str | None = None) -> dict:
    """AI 代理请求头；无凭证时报 LicensingError。"""
    cred = load_credential(fp)
    if not cred:
        raise LicensingError("未激活，无法使用 AI 服务", hint="请先在激活页输入激活码")
    return {"Authorization": f"Bearer {cred['token']}"}
