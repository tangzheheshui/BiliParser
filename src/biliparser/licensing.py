"""客户端鉴权：一次性激活 + 本地 HMAC 验签 + 每次启动联网核验。

按 docs/requirements/客户端需求文档.md v2.1 + 2026-08-29 补充：
- 激活：仅无凭证/校验失败时，由用户在激活窗口输码触发一次联网请求；
- 校验：每次启动本地重算 HMAC-SHA256(sn|mac|activated_at) 比对 token，
  并核对当前机器 MAC——本地部分全程不联网；
- 启动核验（2026-08-29 新增）：每次启动另向服务器上报 {sn, mac}，
  码被后台解绑/已换绑 → 清凭证踢回激活页；服务器不可达 → 离线宽容放行；
- 设备标识：本机 MAC（取第一块有效物理网卡，多网卡固定策略 CR-03）。

安全边界（如实）：签名密钥随客户端分发，理论上可被提取；MAC 也可被伪造。
目标是防「一码多机传播」与随手篡改凭证，不防专业逆向（文档 5.3）。
"""

import hashlib
import hmac
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

import httpx

LICENSE_PATH = Path.home() / ".biliparser" / "license.json"
OFFICIAL_SITE = "https://biliparser.tangzheheshui.cn"   # 品牌名点击跳转的官网（固定字符串，改这里）


class LicensingError(Exception):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


# ---------------- 签名密钥（与服务器 LICENSE_SIGN_KEY 完全一致，CR-04） ----------------

def bundled_text(name: str) -> str:
    """读打包时烧入的文本（_dist_server.txt / _sign_key.txt），没有则空串。

    frozen 包里 biliparser 目录的物理位置随 PyInstaller 版本/布局漂移——
    同一工程出过 Resources/ 与 Frameworks/ 两种，烧入文件跟运行时
    __file__ 不在一起，正式版就被当成直连版显示「免激活」（2026-08-25
    实测翻车）。这里把常见候选位置全试一遍，烧在哪都认；开发环境即包目录。
    """
    here = Path(__file__).parent
    candidates = [here]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates += [Path(meipass) / "biliparser", Path(meipass)]
    top = here.parent.parent                      # .app 的 Contents 目录
    candidates += [top / "Resources" / "biliparser", top / "Frameworks" / "biliparser"]
    for cand in candidates:
        try:
            txt = (cand / name).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if txt:
            return txt
    return ""


def _sign_key() -> str:
    """内置签名密钥：打包时烧入 _sign_key.txt（见 packaging/build-macos.sh）；
    开发时可用环境变量 BILIPARSER_SIGN_KEY 覆盖；都没有用开发默认值
    （与服务器的开发默认一致，本地联调开箱即用）。"""
    key = bundled_text("_sign_key.txt")
    if key:
        return key
    import os
    return os.environ.get("BILIPARSER_SIGN_KEY", "") or "dev-sign-key-change-me"


# ---------------- MAC 地址（设备唯一标识） ----------------

def _format_mac(n: int) -> str:
    return ":".join(f"{(n >> s) & 0xFF:02x}" for s in range(40, -1, -8))


def mac_address() -> str:
    """本机 MAC 原值（如 aa:bb:cc:dd:ee:ff）。取第一块有效物理网卡：

    - macOS：ifconfig 接口列表里第一个 en<N> 且 ether 非零（en0 = 内置网卡）；
    - Windows：getmac 输出的第一条；
    - 其他/兜底：uuid.getnode()（读网络栈的 MAC，非随机时可用）。
    上送给服务器的是原值，规范化（去分隔符大写）由服务器统一做（FR-02）。
    """
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["ifconfig", "-l"], capture_output=True,
                                 text=True, timeout=5).stdout.split()
            for name in out:
                if re.fullmatch(r"en\d+", name):
                    info = subprocess.run(["ifconfig", name], capture_output=True,
                                          text=True, timeout=5).stdout
                    m = re.search(r"ether\s+([0-9a-fA-F:]{17})", info)
                    if m and set(m.group(1)) != {"0", ":"}:   # 排全零
                        return m.group(1).lower()
        except (OSError, subprocess.SubprocessError):
            pass
    elif sys.platform == "win32":
        try:
            # CREATE_NO_WINDOW：窗口化 App 起控制台子进程会闪 cmd 黑框，
            # 而 _gate 每次业务操作都调 mac_address()，不加这行每次都弹一次。
            out = subprocess.run(["getmac", "/fo", "csv", "/nh"],
                                 capture_output=True, text=True, timeout=5,
                                 creationflags=subprocess.CREATE_NO_WINDOW).stdout
            for line in out.splitlines():
                m = re.search(r"([0-9A-Fa-f]{2}[-:]){5}[0-9A-Fa-f]{2}", line or "")
                if m and set(m.group(0)) - {"0", "-", ":"}:
                    return m.group(0)
        except (OSError, subprocess.SubprocessError):
            pass
    n = uuid.getnode()
    if n and not (n & 0x010000000000) and (n & 0xFEFFFFFFFFFF):  # 非组播、非全零
        return _format_mac(n)
    return "00:00:00:00:00:00"          # 兜底占位（服务器会按格式放行/拒绝）


def normalize_mac(raw: str) -> str:
    """与服务器同一规则：去分隔符大写（客户端文档 5.1 步骤 3 用）。"""
    return re.sub(r"[^0-9A-Fa-f]", "", str(raw or "")).upper()


# ---------------- 凭证存储（机器绑定混淆，沿用原机制） ----------------

def _keystream(seed: str, n: int) -> bytes:
    """MAC 派生密钥流：sha256 计数器模式。换台机器密钥不同，拷文件无效。"""
    out = b""
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(f"{seed}:{counter}".encode()).digest()
        counter += 1
    return out[:n]


def _obfuscate(text: str, seed: str) -> str:
    data = text.encode()
    key = _keystream(seed, len(data))
    return bytes(a ^ b for a, b in zip(data, key)).hex()


def _deobfuscate(hex_text: str, seed: str) -> str:
    data = bytes.fromhex(hex_text)
    key = _keystream(seed, len(data))
    return bytes(a ^ b for a, b in zip(data, key)).decode("utf-8", errors="replace")


def _seed() -> str:
    return normalize_mac(mac_address())


def _save(sn: str, mac: str, activated_at: str, token: str) -> None:
    payload = json.dumps({"sn": sn, "mac": mac, "activated_at": activated_at,
                          "token": token}, ensure_ascii=False)
    LICENSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LICENSE_PATH.write_text(
        json.dumps({"v": 2, "data": _obfuscate(payload, _seed())}),
        encoding="utf-8",
    )


def load_credential(seed: str | None = None) -> dict | None:
    """读本地凭证；文件不存在/损坏/换机器（MAC 不同→乱码）/缺任一字段 → None。

    凭证四字段作为整体存取，任一缺失即无效（客户端文档 4.3）。
    """
    if not LICENSE_PATH.exists():
        return None
    try:
        raw = json.loads(LICENSE_PATH.read_text(encoding="utf-8"))
        cred = json.loads(_deobfuscate(raw["data"], seed or _seed()))
        return {k: str(cred[k]) for k in ("sn", "mac", "activated_at", "token")}
    except (KeyError, ValueError, TypeError, OSError):
        return None


def clear_credential() -> None:
    LICENSE_PATH.unlink(missing_ok=True)


# ---------------- 激活（唯一联网交互，一次性） ----------------

def activate(server_url: str, sn: str) -> dict:
    """输码激活：POST /api/v1/license/activate {mac, sn}，成功存凭证。

    返回码按客户端文档 3.3 处理；网络异常/500 统一「网络异常，请稍后再试」
    （已激活用户不受影响：本地凭证仍在，无需再调本函数）。
    """
    try:
        resp = httpx.post(
            server_url.rstrip("/") + "/api/v1/license/activate",
            json={"mac": mac_address(), "sn": str(sn or "").strip()},
            timeout=10,
        )
    except httpx.HTTPError:
        raise LicensingError("网络异常，请稍后再试",
                             hint="检查网络后重试；已激活的设备不受影响")
    try:
        body = resp.json()
        code = body.get("code")
    except ValueError:
        code = 500
    if resp.status_code != 200:
        code = 500
    if code == 0:
        data = body["data"]
        _save(data["sn"], data["mac"], data["activated_at"], data["token"])
        return data
    messages = {
        1: "未激活",
        2: "激活码无效，请检查输入",
        3: "该激活码已被其他设备使用",
        4: "参数错误（MAC 为空或格式非法）",
        429: "请求过于频繁，请稍后再试",
    }
    raise LicensingError(messages.get(code, "网络异常，请稍后再试"))


# ---------------- 启动本地校验（不联网） ----------------

def verify_local(seed: str | None = None, key: str | None = None,
                 current_mac: str | None = None) -> dict:
    """启动校验，返回 {ok, reason}。失败一律走激活流程，不判死（CR-02）。

    1. 四字段齐全；2. 重算 HMAC 与 token 比对；3. 当前 MAC 与凭证 MAC 比对。
    """
    cred = load_credential(seed)
    if not cred:
        return {"ok": False, "reason": "未激活"}
    msg = f"{cred['sn']}|{cred['mac']}|{cred['activated_at']}".encode()
    expect = hmac.new((key or _sign_key()).encode(), msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, cred["token"]):
        return {"ok": False, "reason": "凭证校验失败"}
    mac_now = current_mac if current_mac is not None else normalize_mac(mac_address())
    if mac_now != cred["mac"]:
        return {"ok": False, "reason": "设备不匹配"}
    return {"ok": True, "reason": ""}


# ---------------- 启动联网核验（每次启动；解绑/换绑后老设备失效） ----------------

def verify_remote(server_url: str, seed: str | None = None) -> dict:
    """每次启动把 {sn, mac} 报给服务器核验（2026-08-29 用户拍板：要能远程踢设备）。

    返回 {checked, ok, reason}：
    - checked=False：服务器不可达/响应异常/429 → 离线宽容，维持本地状态
      （保住「离线也能用」；老服务器没有 /verify 也走这里）
    - checked=True, ok=False：服务器明确判 2（码无效）/ 3（设备不匹配，
      即后台已解绑或码已换绑新设备）→ 清掉本地凭证，下次走激活流程
    """
    cred = load_credential(seed)
    if not cred:
        return {"checked": False, "ok": False, "reason": "未激活"}
    try:
        resp = httpx.post(
            server_url.rstrip("/") + "/api/v1/license/verify",
            json={"sn": cred["sn"], "mac": normalize_mac(mac_address())},
            timeout=4,
        )
    except httpx.HTTPError:
        return {"checked": False, "ok": True, "reason": "服务器不可达，离线放行"}
    try:
        code = resp.json().get("code")
    except ValueError:
        code = None
    if resp.status_code != 200 or code is None:
        return {"checked": False, "ok": True, "reason": "服务器响应异常，离线放行"}
    if code == 0:
        return {"checked": True, "ok": True, "reason": ""}
    if code in (2, 3):                                 # 明确吊销 → 清凭证
        clear_credential()
        return {"checked": True, "ok": False,
                "reason": {2: "激活码已失效",
                           3: "该激活码已在其他设备使用"}.get(code, "远程核验未通过")}
    return {"checked": False, "ok": True, "reason": f"核验返回 {code}，离线放行"}
