"""BiliParser 授权服务器：激活码绑定/验证 + AI 调用代理（配额限流）+ 管理后台。

对外 3 个 API（客户端用）：
  POST /api/activate  {code, fingerprint}        → 绑定设备，发 token
  POST /api/verify    {token}                    → 启动验证（含 72h 宽限期下发）
  POST /api/ai/chat   Bearer token, OpenAI 格式   → 配额检查后转发 GLM
  GET  /api/quota     Bearer token               → 今日用量（客户端状态卡）

管理后台（ADMIN_KEY 保护）：/admin 生成/列表/禁用/解绑/配额。

密钥全部走环境变量：SERVER_SECRET / ADMIN_KEY / GLM_API_KEY /
GLM_BASE_URL / GLM_MODEL / LICENSE_DB。部署见 docs/deploy.md。
"""

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
from flask import Flask, g, jsonify, redirect, render_template, request

from db import connect

def _utcnow() -> datetime:
    """utcnow() 在 3.12+ 已弃用，统一走这里（仍返回 naive UTC，与库内字符串比较一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


TOKEN_TTL_DAYS = 365       # token 自身有效期（真正吊销靠数据库 is_active）
GRACE_HOURS = 72           # 客户端离线宽限期


# ---------------- token（HMAC 签名，无状态但可查库吊销） ----------------

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_token(secret: str, license_id: int, fingerprint: str, exp_ts: int) -> str:
    payload = f"{license_id}:{fingerprint}:{exp_ts}".encode()
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(sig)}"


def parse_token(secret: str, token: str) -> tuple[int, str, int] | None:
    """验签拆包：license_id, fingerprint, exp_ts；任何异常返回 None。"""
    try:
        payload_b64, sig_b64 = token.split(".")
        payload = _unb64(payload_b64)
        expect = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expect, _unb64(sig_b64)):
            return None
        license_id, fingerprint, exp_ts = payload.decode().rsplit(":", 2)
        return int(license_id), fingerprint, int(exp_ts)
    except (ValueError, TypeError):
        return None


# ---------------- 应用工厂 ----------------

def create_app(
    db_path: str | None = None,
    server_secret: str | None = None,
    admin_key: str | None = None,
    glm_api_key: str | None = None,
    glm_base_url: str | None = None,
    glm_model: str | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config.update(
        DB_PATH=db_path or os.environ.get("LICENSE_DB", "licenses.db"),
        SERVER_SECRET=server_secret or os.environ.get("SERVER_SECRET", "dev-secret-change-me"),
        ADMIN_KEY=admin_key or os.environ.get("ADMIN_KEY", "dev-admin"),
        GLM_API_KEY=glm_api_key or os.environ.get("GLM_API_KEY", ""),
        GLM_BASE_URL=glm_base_url or os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"),
        GLM_MODEL=glm_model or os.environ.get("GLM_MODEL", "glm-4-flash"),
    )

    def db() -> sqlite3.Connection:
        if "db" not in g:
            g.db = connect(app.config["DB_PATH"])
        return g.db

    app.teardown_appcontext(lambda e: g.pop("db", None).close() if "db" in g else None)

    def err(message: str, status: int, hint: str | None = None):
        return jsonify({"error": message, "hint": hint}), status

    # ---------------- 业务校验 ----------------

    def _license_state(row) -> tuple[bool, str]:
        """激活码当前可用性：is_active + expires_at。"""
        if row is None:
            return False, "激活码无效"
        if not row["is_active"]:
            return False, "激活码已被禁用"
        if row["expires_at"] and row["expires_at"] <= _utcnow().isoformat():
            return False, "激活码已过期"
        return True, ""

    def _check_token(token: str) -> tuple[sqlite3.Row | None, str]:
        """token 验签 + 数据库实时状态（支持远程吊销）。返回 (row, 错误消息)。"""
        if not token:
            return None, "缺少凭证"
        parsed = parse_token(app.config["SERVER_SECRET"], token)
        if not parsed:
            return None, "凭证无效"
        license_id, fingerprint, exp_ts = parsed
        if exp_ts < int(_utcnow().timestamp()):
            return None, "凭证已过期，请重新激活"
        row = db().execute("SELECT * FROM licenses WHERE id=?", (license_id,)).fetchone()
        ok, reason = _license_state(row)
        if not ok:
            return None, reason
        if row["device_fingerprint"] != fingerprint:
            return None, "设备已解绑，请重新激活"
        return row, ""

    def _issue(row) -> str:
        exp_ts = int((_utcnow() + timedelta(days=TOKEN_TTL_DAYS)).timestamp())
        return issue_token(app.config["SERVER_SECRET"], row["id"], row["device_fingerprint"], exp_ts)

    def _usage(row) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        u = db().execute(
            "SELECT count FROM usage WHERE license_id=? AND day=?", (row["id"], today)
        ).fetchone()
        return {"today_used": (u["count"] if u else 0), "daily_quota": row["daily_quota"]}

    # ---------------- 客户端 API ----------------

    @app.post("/api/activate")
    def activate():
        data = request.get_json(silent=True) or {}
        code = str(data.get("code") or "").strip()
        fingerprint = str(data.get("fingerprint") or "").strip()
        if not code or not fingerprint:
            return err("code 和 fingerprint 必填", 400)
        row = db().execute("SELECT * FROM licenses WHERE code=?", (code,)).fetchone()
        ok, reason = _license_state(row)
        if not ok:
            return err(reason, 403, hint="请确认激活码输入无误，或联系卖家")
        if row["device_fingerprint"] and row["device_fingerprint"] != fingerprint:
            return err(
                "该激活码已绑定其他设备（一码一机）",
                403,
                hint="换机请联系卖家在管理后台解绑，再重新激活",
            )
        if not row["device_fingerprint"]:  # 首次激活
            db().execute(
                "UPDATE licenses SET device_fingerprint=?, activated_at=? WHERE id=?",
                (fingerprint, _utcnow().isoformat(), row["id"]),
            )
            db().commit()
        exp_ts = int((_utcnow() + timedelta(days=TOKEN_TTL_DAYS)).timestamp())
        token = issue_token(app.config["SERVER_SECRET"], row["id"], fingerprint, exp_ts)
        return jsonify({
            "success": True, "message": "激活成功",
            "token": token,
            "valid_until": (_utcnow() + timedelta(hours=GRACE_HOURS)).isoformat(),
            "usage": {"today_used": 0, "daily_quota": row["daily_quota"]},
        })

    @app.post("/api/verify")
    def verify():
        data = request.get_json(silent=True) or {}
        row, reason = _check_token(str(data.get("token") or ""))
        if not row:
            return jsonify({"valid": False, "message": reason})
        return jsonify({
            "valid": True,
            "valid_until": (_utcnow() + timedelta(hours=GRACE_HOURS)).isoformat(),
            "usage": _usage(row),
        })

    def _bearer() -> str:
        auth = request.headers.get("Authorization", "")
        return auth[7:] if auth.startswith("Bearer ") else ""

    @app.get("/api/quota")
    def quota():
        row, reason = _check_token(_bearer())
        if not row:
            return err(reason, 403)
        return jsonify(_usage(row))

    @app.post("/api/ai/chat")
    def ai_chat():
        row, reason = _check_token(_bearer())
        if not row:
            return err(reason, 403)
        body = request.get_json(silent=True) or {}
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return err("请求体需要 messages 数组", 400)

        usage_now = _usage(row)
        if usage_now["today_used"] >= usage_now["daily_quota"]:
            return err(
                f"今日 AI 调用已达上限（{usage_now['daily_quota']} 次/天）",
                429,
                hint="明天恢复，或联系卖家调高配额",
            )

        # 转发 GLM：模型由服务器统一指定，客户端传什么都不算数（成本可控）
        if not app.config["GLM_API_KEY"]:
            return err("服务器未配置 GLM_API_KEY", 500)
        try:
            resp = httpx.post(
                app.config["GLM_BASE_URL"].rstrip("/") + "/chat/completions",
                json={"model": app.config["GLM_MODEL"], "messages": messages,
                      "temperature": body.get("temperature", 0.3)},
                headers={"Authorization": f"Bearer {app.config['GLM_API_KEY']}"},
                timeout=180,
            )
        except httpx.HTTPError as e:
            return err(f"上游 AI 请求失败：{e.__class__.__name__}", 502)

        if resp.status_code == 200:  # 成功才计费
            db().execute(
                "INSERT INTO usage (license_id, day, count) VALUES (?,?,1) "
                "ON CONFLICT(license_id, day) DO UPDATE SET count=count+1",
                (row["id"], datetime.now().strftime("%Y-%m-%d")),
            )
            db().commit()
        return app.response_class(resp.text, status=resp.status_code,
                                  mimetype="application/json")

    # ---------------- 管理后台 ----------------

    def _admin_ok() -> bool:
        key = request.headers.get("X-Admin-Key") or request.args.get("key") or request.form.get("key")
        return bool(key) and hmac.compare_digest(key, app.config["ADMIN_KEY"])

    @app.get("/admin")
    def admin_page():
        if not _admin_ok():
            return err("管理密钥错误", 403)
        rows = db().execute(
            "SELECT l.*, COALESCE(u.count,0) AS used_today FROM licenses l "
            "LEFT JOIN usage u ON u.license_id=l.id AND u.day=date('now','localtime') "
            "ORDER BY l.id DESC"
        ).fetchall()
        return render_template("admin.html", rows=rows, key=app.config["ADMIN_KEY"])

    @app.post("/admin/generate")
    def admin_generate():
        if not _admin_ok():
            return err("管理密钥错误", 403)
        count = max(1, min(int(request.form.get("count", 1)), 100))
        note = request.form.get("note", "").strip()
        days = request.form.get("days", "").strip()  # 空 = 永久
        expires = ""
        if days.isdigit() and int(days) > 0:
            expires = (_utcnow() + timedelta(days=int(days))).isoformat()
        codes = []
        for _ in range(count):
            code = "BP-" + "-".join(
                secrets.token_hex(2).upper() for _ in range(4)
            )  # BP-XXXX-XXXX-XXXX-XXXX
            db().execute(
                "INSERT INTO licenses (code, note, expires_at) VALUES (?,?,?)",
                (code, note, expires),
            )
            codes.append(code)
        db().commit()
        return render_template("admin.html", rows=_all_rows(db()), key=app.config["ADMIN_KEY"],
                               generated=codes)

    @app.post("/admin/action")
    def admin_action():
        """禁用/启用/解绑/调配额，op 字段区分。"""
        if not _admin_ok():
            return err("管理密钥错误", 403)
        op = request.form.get("op")
        lid = request.form.get("id")
        if not lid.isdigit():
            return err("参数错误", 400)
        if op == "toggle":
            db().execute("UPDATE licenses SET is_active = 1 - is_active WHERE id=?", (lid,))
        elif op == "unbind":
            # 一码一机的换机通道；unbind_count 防反复白嫖（上限在页面提示）
            db().execute(
                "UPDATE licenses SET device_fingerprint=NULL, activated_at=NULL, "
                "unbind_count=unbind_count+1 WHERE id=?", (lid,))
        elif op == "quota":
            q = request.form.get("daily_quota", "")
            if q.isdigit() and int(q) >= 0:
                db().execute("UPDATE licenses SET daily_quota=? WHERE id=?", (int(q), lid))
        else:
            return err("未知操作", 400)
        db().commit()
        return redirect(f"/admin?key={app.config['ADMIN_KEY']}")

    def _all_rows(dbconn):
        return dbconn.execute(
            "SELECT l.*, COALESCE(u.count,0) AS used_today FROM licenses l "
            "LEFT JOIN usage u ON u.license_id=l.id AND u.day=date('now','localtime') "
            "ORDER BY l.id DESC"
        ).fetchall()

    return app


# 开发运行：python app.py
if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=7900)
