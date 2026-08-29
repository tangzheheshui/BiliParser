"""授权服务器测试：按《服务器需求文档.md》v2.2 第 10 节验收标准逐条覆盖。

另覆盖：限流 429、管理鉴权 401、token 可用同密钥复算、SN 格式与唯一性、
未配置 ADMIN_PASSWORD 的明确报错、HTTP 恒 200 约定。
"""

import re
import sqlite3

import pytest

from app import SN_CHARSET, create_app, normalize_mac, normalize_sn, sign_token

SIGN_KEY = "test-sign-key-0123456789abcdef0123456789"
SN_RE = re.compile(r"^[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{4}(-[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{4}){3}$")
MAC_A_RAW = "aa:bb:cc:dd:ee:ff"
MAC_A = "AABBCCDDEEFF"
MAC_B_RAW = "11-22-33-44-55-66"
MAC_B = "112233445566"


@pytest.fixture()
def server(tmp_path):
    app = create_app(db_path=str(tmp_path / "lic.db"), admin_password="pw",
                     sign_key=SIGN_KEY, restock_threshold=10, restock_target=50)
    c = app.test_client()
    # 首启空库 → 自动生成 50（验收 #1）
    yield c


@pytest.fixture()
def admin(server):
    d = server.post("/api/v1/admin/login", json={"password": "pw"}).get_json()
    return {"Authorization": f"Bearer {d['data']['token']}"}


def _take(server, admin) -> str:
    d = server.post("/api/v1/admin/codes/take", headers=admin).get_json()
    assert d["code"] == 0, d
    return d["data"]["sn"]


def _activate(server, sn: str, mac: str = MAC_A_RAW):
    return server.post("/api/v1/license/activate", json={"mac": mac, "sn": sn})


# ---------- 规范化 ----------

def test_normalize_mac_and_sn():
    assert normalize_mac("AA:BB:CC:DD:EE:FF") == "AABBCCDDEEFF"
    assert normalize_mac("aa:bb:cc:dd:ee:ff") == "AABBCCDDEEFF"
    assert normalize_mac(MAC_B_RAW) == "112233445566"
    assert normalize_mac("") is None
    assert normalize_mac("zz:xx") is None            # 非十六进制
    assert normalize_mac("aabbccddeef") is None      # 11 位
    assert normalize_sn(" abcd-1234-efgh-5678 ") == "ABCD-1234-EFGH-5678"


# ---------- 库存池 / 取码 / 退回 ----------

def test_startup_generates_batch(server, admin):     # 验收 #1
    d = server.get("/api/v1/admin/codes?status=unshipped", headers=admin).get_json()
    assert d["code"] == 0 and d["data"]["unshipped_count"] == 50


def test_take_fifo_marks_shipped(server, admin):     # 验收 #2
    # 列表按 id 倒序、一页拉全，最后一个是 id 最小 = 最旧
    oldest = server.get("/api/v1/admin/codes?status=unshipped&page_size=100",
                        headers=admin).get_json()["data"]["items"][-1]["sn"]
    sn = _take(server, admin)
    assert sn == oldest
    d = server.get("/api/v1/admin/codes?status=shipped", headers=admin).get_json()
    item = next(i for i in d["data"]["items"] if i["sn"] == sn)
    assert item["shipped_at"]                          # 记录发货时间


def test_restock_when_below_threshold(server, admin):  # 验收 #3：连取 41 个 → 9 < 10 → 补回 50
    for _ in range(41):
        _take(server, admin)
    d = server.get("/api/v1/admin/codes?status=unshipped", headers=admin).get_json()
    assert d["data"]["unshipped_count"] == 50


def test_unshipped_count_decrements(server, admin):   # 验收 #4
    before = server.get("/api/v1/admin/codes", headers=admin).get_json()["data"]["unshipped_count"]
    _take(server, admin)
    after = server.get("/api/v1/admin/codes", headers=admin).get_json()["data"]["unshipped_count"]
    assert after == before - 1


def test_take_requires_auth(server):                  # 验收 #15
    d = server.post("/api/v1/admin/codes/take").get_json()
    assert d["code"] == 401


def test_return_shipped_then_reusable(server, admin):  # 验收 #10
    sn = _take(server, admin)
    d = server.post(f"/api/v1/admin/codes/{sn}/return", headers=admin).get_json()
    assert d["code"] == 0
    again = _take(server, admin)                       # 退回后可再次被取出（最旧，立刻回队首）
    assert again == sn


def test_return_activated_rejected(server, admin):    # 验收 #11
    sn = _take(server, admin)
    _activate(server, sn)
    d = server.post(f"/api/v1/admin/codes/{sn}/return", headers=admin).get_json()
    assert d["code"] == 4


# ---------- 激活 ----------

def test_activate_shipped_code(server, admin):        # 验收 #5
    sn = _take(server, admin)
    r = _activate(server, sn)
    assert r.status_code == 200
    d = r.get_json()
    assert d["code"] == 0 and d["message"] == "激活成功"
    data = d["data"]
    assert data["sn"] == sn and data["mac"] == MAC_A
    assert data["token"] and data["activated_at"]
    row = sqlite3.connect(server.application.config["DB_PATH"]).execute(
        "SELECT status, bound_mac FROM license_codes WHERE sn=?", (sn,)).fetchone()
    assert row == ("activated", MAC_A)


def test_activate_unshipped_code_direct(server, admin):  # 验收 #6：跳过取货直接激活也放行
    sn = server.get("/api/v1/admin/codes?status=unshipped&page_size=100",
                    headers=admin).get_json()["data"]["items"][-1]["sn"]
    d = _activate(server, sn).get_json()
    assert d["code"] == 0
    row = sqlite3.connect(server.application.config["DB_PATH"]).execute(
        "SELECT status FROM license_codes WHERE sn=?", (sn,)).fetchone()
    assert row[0] == "activated"


def test_reactivate_same_mac(server, admin):          # 验收 #7：同机重装恢复
    sn = _take(server, admin)
    assert _activate(server, sn).get_json()["code"] == 0
    d2 = _activate(server, sn).get_json()
    assert d2["code"] == 0 and d2["data"]["token"]


def test_activate_other_mac_rejected(server, admin):  # 验收 #8
    sn = _take(server, admin)
    _activate(server, sn)
    d = _activate(server, sn, MAC_B_RAW).get_json()
    assert d["code"] == 3
    row = sqlite3.connect(server.application.config["DB_PATH"]).execute(
        "SELECT bound_mac FROM license_codes WHERE sn=?", (sn,)).fetchone()
    assert row[0] == MAC_A                            # 绑定关系不变


def test_mac_case_insensitive_same_device(server, admin):  # 验收 #9
    sn = _take(server, admin)
    assert _activate(server, sn, "AA:BB:CC:DD:EE:FF").get_json()["code"] == 0
    d = _activate(server, sn, "aa:bb:cc:dd:ee:ff").get_json()
    assert d["code"] == 0


def test_activate_error_codes(server):                # 验收 #12
    assert _activate(server, "", MAC_A_RAW).get_json()["code"] == 1     # sn 空
    d = server.post("/api/v1/license/activate", json={"sn": "X"}).get_json()
    assert d["code"] == 4                                           # mac 缺失
    assert _activate(server, "XXXX-YYYY-ZZZZ-1234").get_json()["code"] == 2  # 不存在
    # 所有业务响应 HTTP 均 200
    assert server.post("/api/v1/license/activate", json={}).status_code == 200


def test_token_verifiable_with_same_key(server, admin):  # 验收 #13
    sn = _take(server, admin)
    data = _activate(server, sn).get_json()["data"]
    assert sign_token(SIGN_KEY, data["sn"], data["mac"], data["activated_at"]) == data["token"]


def test_activate_writes_log(server, admin):          # FR-06：每次激活请求写日志
    sn = _take(server, admin)
    _activate(server, sn)
    _activate(server, sn, MAC_B_RAW)                  # 失败也记
    rows = sqlite3.connect(server.application.config["DB_PATH"]).execute(
        "SELECT result_code FROM activate_logs WHERE sn=? ORDER BY id", (sn,)).fetchall()
    assert [r[0] for r in rows] == [0, 3]


# ---------- SN 生成 ----------

def test_sn_format_charset_unique(server, admin):     # 验收 #14
    sns = [_take(server, admin) for _ in range(60)]   # 触发一轮补货
    assert all(SN_RE.match(s) for s in sns)
    assert len(set(sns)) == len(sns)                  # 全局唯一
    assert all(c in SN_CHARSET for s in sns for c in s.replace("-", ""))


# ---------- 管理鉴权 ----------

def test_admin_login_wrong_password(server):
    d = server.post("/api/v1/admin/login", json={"password": "bad"}).get_json()
    assert d["code"] == 401


def test_admin_unconfigured_password(tmp_path):
    app = create_app(db_path=str(tmp_path / "x.db"), admin_password="",
                     sign_key=SIGN_KEY)
    d = app.test_client().post("/api/v1/admin/login",
                               json={"password": "x"}).get_json()
    assert d["code"] == 500 and "ADMIN_PASSWORD" in d["message"]


def test_list_pagination_and_filters(server, admin):
    d = server.get("/api/v1/admin/codes?page=1&page_size=10", headers=admin).get_json()
    assert d["code"] == 0 and len(d["data"]["items"]) == 10
    assert d["data"]["total"] == 50
    d2 = server.get("/api/v1/admin/codes?page=2&page_size=10", headers=admin).get_json()
    sns1 = {i["sn"] for i in d["data"]["items"]}
    sns2 = {i["sn"] for i in d2["data"]["items"]}
    assert not sns1 & sns2                            # 分页不重叠


# ---------- 限流 ----------

def test_activate_rate_limited(server):
    for i in range(60):
        assert _activate(server, "SN-NOT-EXIST").get_json()["code"] == 2
    d = _activate(server, "SN-NOT-EXIST").get_json()  # 第 61 次
    assert d["code"] == 429


# ---------- 持久化 ----------

def test_restart_persistence(tmp_path):               # 验收 #16：重启后数据一致
    db = str(tmp_path / "lic.db")
    app1 = create_app(db_path=db, admin_password="pw", sign_key=SIGN_KEY)
    c1 = app1.test_client()
    t = c1.post("/api/v1/admin/login", json={"password": "pw"}).get_json()["data"]["token"]
    h = {"Authorization": f"Bearer {t}"}
    sn = c1.post("/api/v1/admin/codes/take", headers=h).get_json()["data"]["sn"]
    assert c1.post("/api/v1/license/activate",
                   json={"mac": MAC_A_RAW, "sn": sn}).get_json()["code"] == 0

    app2 = create_app(db_path=db, admin_password="pw", sign_key=SIGN_KEY)  # 重启
    c2 = app2.test_client()
    t2 = c2.post("/api/v1/admin/login", json={"password": "pw"}).get_json()["data"]["token"]
    h2 = {"Authorization": f"Bearer {t2}"}
    d = c2.get("/api/v1/admin/codes?status=activated", headers=h2).get_json()
    assert [i["sn"] for i in d["data"]["items"]] == [sn]      # 取码状态仍在
    # 重启后同机再激活仍 code=0
    assert c2.post("/api/v1/license/activate",
                   json={"mac": "AA:BB:CC:DD:EE:FF", "sn": sn}).get_json()["code"] == 0


def test_download_page_addresses(server):
    """下载页住在 /download（用户拍板）：根路径与旧 /site 都转过去；
    /download 本身是页面，/download/<文件> 仍是安装包直链。"""
    for u in ("/", "/site", "/site/whatever.html"):
        r = server.get(u)
        assert r.status_code == 302 and r.headers["Location"].endswith("/download"), u
    r = server.get("/download")
    assert r.status_code == 200 and "BiliParser" in r.get_data(as_text=True)
    r = server.get("/")


def test_unbind_allows_reactivation(server, admin):
    """售后解绑：已激活的码解绑后清掉旧 MAC，可换新设备重新激活。"""
    sn = _take(server, admin)
    mac_a = "AA:BB:CC:00:00:01"
    d = server.post("/api/v1/license/activate",
                    json={"sn": sn, "mac": mac_a}).get_json()
    assert d["code"] == 0
    # 他机激活 → 拒绝（设备不匹配）
    d = server.post("/api/v1/license/activate",
                    json={"sn": sn, "mac": "AA:BB:CC:00:00:02"}).get_json()
    assert d["code"] == 3
    # 解绑后 → 新设备可激活
    d = server.post(f"/api/v1/admin/codes/{sn}/unbind", headers=admin).get_json()
    assert d["code"] == 0, d
    d = server.post("/api/v1/license/activate",
                    json={"sn": sn, "mac": "AA:BB:CC:00:00:02"}).get_json()
    assert d["code"] == 0 and d["data"]["mac"] == "AABBCC000002"


def test_unbind_only_for_activated(server, admin):
    """未激活的码解绑 → 业务错误（谈不上解绑）。"""
    sn = _take(server, admin)   # 已发货未激活
    d = server.post(f"/api/v1/admin/codes/{sn}/unbind", headers=admin).get_json()
    assert d["code"] == 4


def test_codes_list_search_by_sn(server, admin):
    """SN 片段搜索（售后找码）。"""
    sn = _take(server, admin)
    frag = sn[5:10]                       # 中间一段
    d = server.get(f"/api/v1/admin/codes?q={frag}", headers=admin).get_json()
    assert d["code"] == 0
    assert any(it["sn"] == sn for it in d["data"]["items"])
    d = server.get("/api/v1/admin/codes?q=ZZZZZZ", headers=admin).get_json()
    assert d["data"]["total"] == 0


def test_admin_token_valid_across_workers(tmp_path):
    """多 worker 回归：登录发的 token 换一个实例（不同内存）也认——
    线上 gunicorn -w 4，内存 dict 各存各的曾导致每个操作都弹登录页。"""
    db = str(tmp_path / "lic.db")
    a = create_app(db_path=db, admin_password="pw", sign_key=SIGN_KEY).test_client()
    b = create_app(db_path=db, admin_password="pw", sign_key=SIGN_KEY).test_client()
    d = a.post("/api/v1/admin/login", json={"password": "pw"}).get_json()
    tok = {"Authorization": f"Bearer {d['data']['token']}"}
    r = b.get("/api/v1/admin/codes", headers=tok).get_json()
    assert r["code"] == 0 and "items" in r["data"]   # 业务码判成功（HTTP 恒 200）


def test_admin_token_expired_or_wrong_password(tmp_path):
    """过期 token / 换密码后的旧 token → 401。"""
    import hashlib
    import hmac as _hmac
    from datetime import datetime, timedelta

    def craft(pw: str, exp: str) -> str:
        sig = _hmac.new(pw.encode(), f"admin|{exp}".encode(), hashlib.sha256).hexdigest()
        return f"{exp}.{sig}"

    db = str(tmp_path / "lic.db")
    c = create_app(db_path=db, admin_password="pw", sign_key=SIGN_KEY).test_client()
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d%H%M%S")
    r = c.get("/api/v1/admin/codes",
              headers={"Authorization": f"Bearer {craft('pw', yesterday)}"}).get_json()
    assert r["code"] == 401
    # 换密码后旧 token 全失效（改 ADMIN_PASSWORD = 踢掉所有登录）
    future = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d%H%M%S")
    c2 = create_app(db_path=db, admin_password="new-pw", sign_key=SIGN_KEY).test_client()
    r = c2.get("/api/v1/admin/codes",
               headers={"Authorization": f"Bearer {craft('pw', future)}"}).get_json()
    assert r["code"] == 401


def test_normalize_sn_dashless_fallback():
    """裸码容错：没粘上分隔符的 16 位码自动补回标准格式（0.2.1 翻车兜底）。"""
    assert normalize_sn("kgndz7gby4w3uutk") == "KGND-Z7GB-Y4W3-UUTK"
    assert normalize_sn(" KGND-Z7GB-Y4W3-UUTK ") == "KGND-Z7GB-Y4W3-UUTK"


def test_activate_dashless_sn(server, admin):
    """客户端把 - 删掉后发的裸码也能激活成功。"""
    sn = _take(server, admin)
    d = server.post("/api/v1/license/activate",
                    json={"mac": MAC_A_RAW, "sn": sn.replace("-", "")}).get_json()
    assert d["code"] == 0 and d["data"]["sn"] == sn


# ---------- 启动核验 verify（远程吊销的落点） ----------

def _verify(server, sn, mac):
    return server.post("/api/v1/license/verify",
                       json={"sn": sn, "mac": mac}).get_json()


def test_verify_ok_while_bound(server, admin):
    sn = _take(server, admin)
    _activate(server, sn)
    assert _verify(server, sn, MAC_A_RAW)["code"] == 0


def test_verify_after_unbind_denied(server, admin):
    """解绑后老设备核验 → 3：远程吊销生效（用户拍板：每次启动都要校验）。"""
    sn = _take(server, admin)
    _activate(server, sn)
    d = server.post(f"/api/v1/admin/codes/{sn}/unbind", headers=admin).get_json()
    assert d["code"] == 0
    assert _verify(server, sn, MAC_A_RAW)["code"] == 3


def test_verify_after_rebind_old_device_denied(server, admin):
    """解绑换新设备后：老设备 3、新设备 0。"""
    sn = _take(server, admin)
    _activate(server, sn)
    server.post(f"/api/v1/admin/codes/{sn}/unbind", headers=admin)
    assert _activate(server, sn, MAC_B_RAW).get_json()["code"] == 0
    assert _verify(server, sn, MAC_A_RAW)["code"] == 3
    assert _verify(server, sn, MAC_B_RAW)["code"] == 0


def test_verify_error_codes(server):
    assert _verify(server, "XXXX-YYYY-ZZZZ-1234", MAC_A_RAW)["code"] == 2  # 不存在
    assert _verify(server, "", MAC_A_RAW)["code"] == 4                    # sn 空
    assert _verify(server, "XXXX", "zz")["code"] == 4                     # MAC 非法


def test_verify_rate_limit_shared_with_activate(server):
    """verify 与 activate 共限流桶：激活打满 60 次后 verify 也 429。"""
    for _ in range(60):
        _activate(server, "SN-NOT-EXIST")
    assert _verify(server, "SN-NOT-EXIST", MAC_A_RAW)["code"] == 429
