"""BiliParser 授权服务器：激活码库存管理 + 一次性激活鉴权。

按 docs/requirements/服务器需求文档.md v2.2 实现（Flask + SQLite，框架沿用旧版）：
- POST /api/v1/license/activate        客户端激活（匿名，每 IP 每分钟 60 次限流）
- POST /api/v1/license/verify          客户端每次启动核验（解绑/换绑后老设备失效）
- POST /api/v1/admin/login             管理员登录（ADMIN_PASSWORD → 7 天 Token）
- POST /api/v1/admin/codes/take        取码发货（FIFO，原子，自动补货）
- POST /api/v1/admin/codes/{sn}/return 退回（仅已发货未激活）
- GET  /api/v1/admin/codes             激活码列表（三态筛选 / 分页 / 库存余量）
- GET  /admin                          单页管理后台

约定：所有业务响应 HTTP 状态码统一 200，业务结果放 body.code。
激活凭证本地 HMAC 验签（离线可用）；客户端每次启动调 /verify 联网核验，
码被解绑/换绑后老设备下次启动即失效；服务器不可达时客户端离线宽容放行。
"""

import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from flask import Flask, jsonify, redirect, request, send_file, send_from_directory, url_for

from db import connect

# ---------------- 常量与环境 ----------------

SN_CHARSET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # 剔除 0/O/1/I/L 易混淆字符
ADMIN_TOKEN_DAYS = 7        # 管理登录有效期（FR-42：≥ 7 天）
ACTIVATE_RATE_LIMIT = 60    # 每 IP 每分钟（NFR-03）
ACTIVATE_RATE_WINDOW = 60

HERE = Path(__file__).parent


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _setup_logger() -> logging.Logger:
    """业务日志：每周轮转、保留 4 个备份；绝不打密钥。"""
    logger = logging.getLogger("license")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    path = Path(os.environ.get("APP_LOG", HERE / "logs" / "app.log"))
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(path, when="D", interval=7, backupCount=4)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


log = _setup_logger()


def _client_ip() -> str:
    """真实客户端 IP（限流用）：直连 7900 就是 remote_addr；
    走 nginx 反代时 remote_addr 恒为 127.0.0.1，取 X-Forwarded-For
    末段（nginx $proxy_add_x_forwarded_for 追加的真实对端，客户端伪造不了）。
    """
    ra = request.remote_addr or ""
    if ra in ("127.0.0.1", "::1"):
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[-1].strip()
    return ra


def _now() -> str:
    """统一本地时间格式（进 HMAC，客户端原样回传，两边必须一字不差）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_mac(raw: str) -> str | None:
    """MAC 规范化（FR-02）：忽略大小写与 : / - 分隔符，统一去分隔符大写。

    非 12 位十六进制 → None（视为参数错误）。
    """
    if not raw:
        return None
    norm = re.sub(r"[^0-9A-Fa-f]", "", str(raw)).upper()
    if len(norm) != 12:
        return None
    return norm


def normalize_sn(raw: str) -> str:
    """激活码规范化（FR-03）：忽略大小写与首尾空格，库内统一大写。

    容错：没带分隔符的 16 位裸码自动补回 XXXX-XXXX-XXXX-XXXX——
    0.2.1 客户端激活页曾把 - 当垃圾字符实时删掉，输码全线失败，服务端兜底。
    """
    s = str(raw or "").strip().upper()
    compact = re.sub(r"[^0-9A-Z]", "", s)
    if "-" not in s and len(compact) == 16:
        return "-".join(compact[i:i + 4] for i in range(0, 16, 4))
    return s


def gen_sn(conn: sqlite3.Connection) -> str:
    """生成 XXXX-XXXX-XXXX-XXXX（FR-13）：secrets 随机 + 入库前查重。"""
    while True:
        sn = "-".join("".join(secrets.choice(SN_CHARSET) for _ in range(4))
                      for _ in range(4))
        if not conn.execute("SELECT 1 FROM license_codes WHERE sn=?", (sn,)).fetchone():
            return sn


def sign_token(key: str, sn: str, mac: str, activated_at: str) -> str:
    """FR-04：token = HMAC-SHA256(secret, sn|mac_normalized|activated_at)。"""
    msg = f"{sn}|{mac}|{activated_at}".encode()
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


# ---------------- 应用工厂 ----------------

def create_app(db_path: str | None = None,
               admin_password: str | None = None,
               sign_key: str | None = None,
               restock_threshold: int | None = None,
               restock_target: int | None = None) -> Flask:
    app = Flask(__name__)

    app.config["DB_PATH"] = db_path or os.environ.get("LICENSE_DB", str(HERE / "licenses.db"))
    app.config["ADMIN_PASSWORD"] = admin_password if admin_password is not None \
        else os.environ.get("ADMIN_PASSWORD", "")
    app.config["SIGN_KEY"] = sign_key if sign_key is not None \
        else os.environ.get("LICENSE_SIGN_KEY", "")
    app.config["RESTOCK_THRESHOLD"] = restock_threshold \
        if restock_threshold is not None else _env_int("RESTOCK_THRESHOLD", 10)
    app.config["RESTOCK_TARGET"] = restock_target \
        if restock_target is not None else _env_int("RESTOCK_TARGET", 50)
    if not app.config["SIGN_KEY"]:
        # 开发默认值（生产必须设 LICENSE_SIGN_KEY，见部署文档；缺省时记录告警）
        app.config["SIGN_KEY"] = "dev-sign-key-change-me"
        log.warning("LICENSE_SIGN_KEY 未配置，使用开发默认值（生产环境必须配置）")

    _db_local = threading.local()

    def db() -> sqlite3.Connection:
        conn = getattr(_db_local, "conn", None)
        if conn is None:
            conn = connect(app.config["DB_PATH"])
            _db_local.conn = conn
        return conn

    _db_lock = threading.Lock()          # 取码 / 补货的原子性（NFR-07）
    _rate: dict[str, deque] = defaultdict(deque)

    # ---------- 激活码生成 / 补货 ----------

    def _restock(force: bool = False) -> int:
        """补货到目标值，返回新生成数量。force=True 用于空库首启。"""
        with _db_lock:
            conn = db()
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "SELECT COUNT(*) FROM license_codes WHERE status='unshipped'"
                ).fetchone()[0]
                if not force and cur >= app.config["RESTOCK_THRESHOLD"]:
                    conn.execute("COMMIT")
                    return 0
                need = max(0, app.config["RESTOCK_TARGET"] - cur)
                remark = f"自动补货 {datetime.now().strftime('%Y-%m-%d')}"
                for _ in range(need):
                    conn.execute(
                        "INSERT INTO license_codes(sn, remark) VALUES(?, ?)",
                        (gen_sn(conn), remark),
                    )
                conn.execute("COMMIT")
                if need:
                    log.info("restock +%d（未发货库存 %d → %d）", need, cur, cur + need)
                return need
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def _db_empty() -> bool:
        with _db_lock:
            conn = db()
            empty = conn.execute("SELECT COUNT(*) FROM license_codes").fetchone()[0] == 0
            conn.commit()
            return empty

    if _db_empty():                                      # FR-10：空库首启自动生成一批
        _restock(force=True)

    # ---------- 响应封装 ----------

    def ok(data: dict | None = None, message: str = "ok"):
        return jsonify({"code": 0, "message": message, "data": data or {}})

    def fail(code: int, message: str):
        return jsonify({"code": code, "message": message, "data": {}})

    # ---------- 激活接口（匿名，核心） ----------

    @app.post("/api/v1/license/activate")
    def activate():
        body = request.get_json(silent=True) or {}
        sn_raw = body.get("sn")
        mac_raw = body.get("mac")
        ip = _client_ip()

        # NFR-03 限流：每 IP 每分钟 ≤ 60 次（滑动窗口）
        now = time.monotonic()
        q = _rate[ip]
        while q and now - q[0] > ACTIVATE_RATE_WINDOW:
            q.popleft()
        if len(q) >= ACTIVATE_RATE_LIMIT:
            _log_activate(sn_raw, mac_raw, 429, ip)
            return fail(429, "请求过于频繁，请稍后再试")
        q.append(now)

        # FR-01 判定表（自上而下）
        if not sn_raw or not str(sn_raw).strip():          # 1 sn 缺失/为空
            _log_activate(sn_raw, mac_raw, 1, ip)
            return fail(1, "未激活")
        if not mac_raw or not str(mac_raw).strip():        # 2 mac 缺失/为空
            _log_activate(sn_raw, mac_raw, 4, ip)
            return fail(4, "参数错误：MAC 为空")

        sn = normalize_sn(sn_raw)
        mac = normalize_mac(str(mac_raw))
        if not mac:                                        # 2b 非法 MAC
            _log_activate(sn_raw, mac_raw, 4, ip)
            return fail(4, "参数错误：MAC 格式非法")

        row = db().execute(
            "SELECT * FROM license_codes WHERE sn=?", (sn,)
        ).fetchone()
        if not row:                                        # 3 码不存在
            _log_activate(sn, mac_raw, 2, ip)
            return fail(2, "无效激活码")

        activated_at = _now()
        token = sign_token(app.config["SIGN_KEY"], sn, mac, activated_at)
        data = {"sn": sn, "mac": mac, "activated_at": activated_at, "token": token}

        if not row["bound_mac"]:                           # 4 未绑定 → 绑定激活
            with _db_lock:
                db().execute(
                    "UPDATE license_codes SET status='activated', bound_mac=?,"
                    " activated_at=? WHERE id=?",
                    (mac, activated_at, row["id"]),
                )
                db().commit()
            _log_activate(sn, mac_raw, 0, ip)
            log.info("activate ok sn=%s mac=%s（新绑定）", sn, mac)
            return ok(data, "激活成功")

        if row["bound_mac"] == mac:                        # 5 同机恢复 → 放行
            with _db_lock:
                db().execute(
                    "UPDATE license_codes SET activated_at=? WHERE id=?",
                    (activated_at, row["id"]),
                )
                db().commit()
            _log_activate(sn, mac_raw, 0, ip)
            log.info("activate ok sn=%s mac=%s（同机恢复，重发凭证）", sn, mac)
            return ok(data, "激活成功")

        _log_activate(sn, mac_raw, 3, ip)                  # 6 他机 → 拒绝
        log.warning("activate denied sn=%s mac=%s（已绑定 %s）", sn, mac, row["bound_mac"])
        return fail(3, "设备不匹配：该激活码已被其他设备使用")

    def _log_activate(sn, mac, code: int, ip: str) -> None:
        """FR-06：每次激活请求写日志，无论成败（绝不打密钥）。"""
        try:
            with _db_lock:
                db().execute(
                    "INSERT INTO activate_logs(sn, mac, result_code, ip)"
                    " VALUES(?, ?, ?, ?)",
                    (str(sn or ""), str(mac or ""), code, ip),
                )
                db().commit()
        except sqlite3.Error:  # 日志失败不阻断业务
            log.exception("activate_logs 写入失败")

    # ---------- 启动核验（每次启动，远程吊销的落点） ----------

    @app.post("/api/v1/license/verify")
    def license_verify():
        """客户端每次启动上报 {sn, mac}：
        绑定匹配 → code 0；解绑后（bound_mac 清空）或已换绑 → code 3（客户端清凭证）；
        码不存在 → code 2。限流与 activate 共桶（启动频率远低于阈值）。
        """
        body = request.get_json(silent=True) or {}
        sn_raw, mac_raw = body.get("sn"), body.get("mac")
        ip = _client_ip()

        now = time.monotonic()
        q = _rate[ip]
        while q and now - q[0] > ACTIVATE_RATE_WINDOW:
            q.popleft()
        if len(q) >= ACTIVATE_RATE_LIMIT:
            return fail(429, "请求过于频繁，请稍后再试")
        q.append(now)

        sn = normalize_sn(sn_raw)
        mac = normalize_mac(str(mac_raw or ""))
        if not sn or not mac:
            return fail(4, "参数错误：sn 或 MAC 为空/格式非法")

        row = db().execute(
            "SELECT * FROM license_codes WHERE sn=?", (sn,)
        ).fetchone()
        if not row:
            log.info("verify deny sn=%s mac=%s（码不存在）", sn, mac)
            return fail(2, "无效激活码")
        if row["bound_mac"] != mac:                     # 含解绑后的 NULL
            log.info("verify deny sn=%s mac=%s（绑定 %s）", sn, mac, row["bound_mac"])
            return fail(3, "设备不匹配：该激活码未绑定当前设备")
        return ok({"sn": sn}, "验证通过")

    # ---------- 管理员鉴权 ----------

    @app.post("/api/v1/admin/login")
    def admin_login():
        if not app.config["ADMIN_PASSWORD"]:               # FR-41 未配置明确报错
            return fail(500, "服务器未配置 ADMIN_PASSWORD，管理功能不可用")
        body = request.get_json(silent=True) or {}
        password = str(body.get("password") or "")
        if not hmac.compare_digest(password, app.config["ADMIN_PASSWORD"]):
            return fail(401, "密码错误")
        token, expires_at = _issue_admin_token()
        log.info("admin login ok")
        return ok({"token": token, "expires_at": expires_at})

    def _issue_admin_token() -> tuple[str, str]:
        """无状态管理 token：exp.HMAC(ADMIN_PASSWORD, "admin|exp")。

        线上 gunicorn 多 worker，内存 dict 各存各的 → 登录后下一个请求打到
        别的 worker 就 401（实测「每个操作都弹登录页」）。改为不落状态的
        签名 token，任何 worker 都能独立验证。改 ADMIN_PASSWORD 即全员失效。
        """
        expires = datetime.now() + timedelta(days=ADMIN_TOKEN_DAYS)
        exp = expires.strftime("%Y%m%d%H%M%S")
        sig = hmac.new(
            (app.config["ADMIN_PASSWORD"] or "").encode(),
            f"admin|{exp}".encode(), hashlib.sha256,
        ).hexdigest()
        return f"{exp}.{sig}", expires.strftime("%Y-%m-%d %H:%M:%S")

    def _admin_ok() -> bool:
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if not token or "." not in token:
            return False
        exp, sig = token.split(".", 1)
        expect = hmac.new(
            (app.config["ADMIN_PASSWORD"] or "").encode(),
            f"admin|{exp}".encode(), hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return False
        try:
            return datetime.strptime(exp, "%Y%m%d%H%M%S") > datetime.now()
        except ValueError:
            return False

    # ---------- 取码发货 ----------

    @app.post("/api/v1/admin/codes/take")
    def codes_take():
        if not _admin_ok():
            return fail(401, "未授权")
        with _db_lock:
            conn = db()
            conn.execute("BEGIN IMMEDIATE")
            try:
                # FIFO 取最旧的未发货码；rowcount 即原子性凭据（CAS）
                row = conn.execute(
                    "SELECT id, sn FROM license_codes"
                    " WHERE status='unshipped' ORDER BY id LIMIT 1"
                ).fetchone()
                if not row:
                    conn.execute("COMMIT")
                    return fail(501, "库存为空且补货失败，请联系管理员")
                shipped_at = _now()
                cur = conn.execute(
                    "UPDATE license_codes SET status='shipped', shipped_at=?"
                    " WHERE id=? AND status='unshipped'",
                    (shipped_at, row["id"]),
                )
                if cur.rowcount != 1:                      # 并发下被别人取走 → 冲突
                    conn.execute("COMMIT")
                    return fail(501, "取码冲突，请重试")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        _restock()                                         # FR-10：低于阈值自动补货
        log.info("take sn=%s（FIFO id=%s）", row["sn"], row["id"])
        return ok({"sn": row["sn"], "shipped_at": shipped_at})

    # ---------- 退回 ----------

    @app.post("/api/v1/admin/codes/<sn>/return")
    def codes_return(sn: str):
        if not _admin_ok():
            return fail(401, "未授权")
        norm = normalize_sn(sn)
        with _db_lock:
            cur = db().execute(
                "UPDATE license_codes SET status='unshipped', shipped_at=NULL"
                " WHERE sn=? AND status='shipped' AND bound_mac IS NULL",
                (norm,),
            )
            db().commit()
        if cur.rowcount != 1:                              # FR-23：已激活不可退回
            return fail(4, "仅已发货且未激活的码可退回")
        log.info("return sn=%s", norm)
        return ok(message="已退回未发货")

    # ---------- 解绑（换电脑售后：清掉绑定，码回到已发货可再激活） ----------

    @app.post("/api/v1/admin/codes/<sn>/unbind")
    def codes_unbind(sn: str):
        if not _admin_ok():
            return fail(401, "未授权")
        norm = normalize_sn(sn)
        with _db_lock:
            cur = db().execute(
                "UPDATE license_codes SET status='shipped', bound_mac=NULL,"
                " activated_at=NULL WHERE sn=? AND status='activated'",
                (norm,),
            )
            db().commit()
        if cur.rowcount != 1:                              # 未激活的码谈不上解绑
            return fail(4, "仅已激活的码可解绑")
        log.info("unbind sn=%s", norm)
        return ok(message="已解绑，买家可重新激活")

    # ---------- 列表 ----------

    @app.get("/api/v1/admin/codes")
    def codes_list():
        if not _admin_ok():
            return fail(401, "未授权")
        try:
            page = max(1, int(request.args.get("page", 1)))
            page_size = min(100, max(1, int(request.args.get("page_size", 20))))
        except ValueError:
            page, page_size = 1, 20
        where, params = "", []
        q = str(request.args.get("q", "")).strip()
        status = request.args.get("status", "all")
        conds = []
        if q:                                              # 按 SN 片段找码（售后解绑用）
            conds.append("sn LIKE ?")
            params.append(f"%{q.upper()}%")
        if status in ("unshipped", "shipped", "activated"):
            conds.append("status=?")
            params.append(status)
        if conds:
            where = "WHERE " + " AND ".join(conds)
        conn = db()
        total = conn.execute(
            f"SELECT COUNT(*) FROM license_codes {where}", params
        ).fetchone()[0]
        unshipped = conn.execute(
            "SELECT COUNT(*) FROM license_codes WHERE status='unshipped'"
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM license_codes {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [page_size, (page - 1) * page_size],
        ).fetchall()
        items = [{
            "sn": r["sn"], "status": r["status"], "bound_mac": r["bound_mac"],
            "shipped_at": r["shipped_at"], "activated_at": r["activated_at"],
        } for r in rows]
        return ok({
            "total": total, "unshipped_count": unshipped,
            "page": page, "page_size": page_size, "items": items,
        })

    # ---------- 管理后台页面 + 官网下载页 ----------

    @app.get("/admin")
    def admin_page():
        return send_from_directory(HERE / "static", "admin.html")

    @app.get("/")
    def root_page():
        """根路径 → 下载页（客户端品牌名点这里）。"""
        return redirect(url_for("download_page"))

    @app.get("/site")
    @app.get("/site/")
    @app.get("/site/<path:fname>")
    def site(fname: str = "index.html"):
        """旧地址兼容：/site 系列一律转去 /download。"""
        return redirect(url_for("download_page"))

    @app.get("/download")
    def download_page():
        """下载页（原 /site）：内容在 static-site/，文件在 /download/<fname>。
        只留无斜杠路由：页面内链接全是相对路径，带斜杠访问会 308 到这里。"""
        return send_from_directory(HERE / "static-site", "index.html")

    @app.get("/download/")
    def download_page_slash():
        return redirect(url_for("download_page"), 308)

    @app.get("/download/<path:fname>")
    def download(fname: str):
        """安装包下发（send_from_directory 自带路径穿越防护）。"""
        return send_from_directory(HERE / "downloads", fname)

    @app.get("/assets/<path:fname>")
    def assets(fname: str):
        """官网静态资源（截图等，static-site/assets/）。"""
        return send_from_directory(HERE / "static-site" / "assets", fname)

    # 可选能力：挂子目录部署（URL_PREFIX=/xxx）时剥前缀 + 设 SCRIPT_NAME，
    # url_for/redirect 自动带前缀；页面内部链接全用相对路径，两种部署通吃。
    # 正式部署是独立子域 biliparser.tangzheheshui.cn 根路径（2026-09-07 起），
    # URL_PREFIX 不设 → 此处零行为；裸 IP:7900 直连（老客户端烧的激活地址）不变。
    prefix = (os.environ.get("URL_PREFIX") or "").rstrip("/")
    if prefix:
        _wsgi = app.wsgi_app

        def _prefixed(environ, start_response):
            path = environ.get("PATH_INFO", "")
            if path == prefix or path.startswith(prefix + "/"):
                environ["SCRIPT_NAME"] = prefix
                environ["PATH_INFO"] = path[len(prefix):] or "/"
            return _wsgi(environ, start_response)

        app.wsgi_app = _prefixed

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=7900)
