"""BiliParser 网页版（托管多用户）：激活码登录 + 每用户 SESSDATA + AI 走授权代理。

与授权服务器部署在同一台机器（gunicorn :7842），复用同一个 licenses.db：
  - 登录：POST /api/web/login 验码拿 WEB token，存 Flask 签名 cookie（30 天）
  - AI  ：以会话 token 走授权服务器 /api/ai/chat，与桌面版共享同一份每日配额
  - B 站：用用户自己的 SESSDATA 从本机出口拉字幕（用户的号、用户的风险，
          所有网页用户的 B 站请求都从本机 IP 出去——规模大时有风控风险）

路由形状与本地 web.py 工作台一致，前端 static/index.html 两边共用。
新增表（db.py）：user_secrets（SESSDATA 加密）、prompts（每码自定义模板）。
"""

import hashlib
import os
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from flask import Flask, g, jsonify, request, session
from werkzeug.exceptions import HTTPException

_here = Path(__file__).resolve().parent
_CORE = _here / "biliparser"                      # 服务器部署布局（vendored 拷贝）
if not _CORE.exists():
    _CORE = _here.parent / "src" / "biliparser"   # 仓库内开发/测试布局
sys.path.insert(0, str(_CORE.parent))

from biliparser import bilibili, meta, subtitle, summarizer  # noqa: E402
from app import parse_token  # noqa: E402  token 验签复用授权服务器实现
from db import connect       # noqa: E402


class ApiError(Exception):
    def __init__(self, message: str, hint: str | None = None, status: int = 400):
        super().__init__(message)
        self.hint = hint
        self.status = status


MAX_WEB_SESSIONS = 2  # 一个激活码同时最多 2 个网页会话，防共享

# 买家可选 AI 提供商（都走 OpenAI 兼容 /chat/completions）
PROVIDERS = {
    "zhipu": {"label": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4/",
              "model": "glm-4.7-flash"},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
                 "model": "deepseek-chat"},
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_app(
    db_path: str | None = None,
    server_secret: str | None = None,
    license_server_url: str | None = None,
) -> Flask:
    app = Flask(__name__, static_folder=None)  # 禁用 Flask 默认 /static，用下面的 static_file 路由
    app.config.update(
        DB_PATH=db_path or os.environ.get("LICENSE_DB", "licenses.db"),
        SERVER_SECRET=server_secret or os.environ.get("SERVER_SECRET", "dev-secret-change-me"),
        LICENSE_SERVER_URL=(license_server_url
                            or os.environ.get("LICENSE_SERVER_URL", "http://127.0.0.1:7900")).rstrip("/"),
    )
    app.secret_key = app.config["SERVER_SECRET"]
    app.permanent_session_lifetime = timedelta(days=30)

    def db():
        if "db" not in g:
            g.db = connect(app.config["DB_PATH"])
        return g.db

    app.teardown_appcontext(lambda e: g.pop("db", None).close() if "db" in g else None)

    def _err(message: str, status: int = 400, hint: str | None = None):
        return jsonify({"error": message, "hint": hint}), status

    # ---------------- 登录门与会话 ----------------

    @app.before_request
    def _gate():
        p = request.path
        if p in ("/", "/favicon.ico", "/api/login", "/api/logout") or p.startswith("/static/"):
            return None
        if not session.get("token"):
            return _err("未登录", 401, hint="请输入激活码登录")
        return None

    def _license():
        """会话 token → 实时校验后的 licenses 行；失效清 session 并抛错。"""
        parsed = parse_token(app.config["SERVER_SECRET"], session["token"])
        if not parsed or parsed[2] < int(time.time()):
            session.clear()
            raise ApiError("登录已失效", status=401, hint="请重新登录")
        license_id = parsed[0]
        row = db().execute("SELECT * FROM licenses WHERE id=?", (license_id,)).fetchone()
        if row is None or not row["is_active"]:
            session.clear()
            raise ApiError("激活码已被禁用", status=403, hint="如有疑问请联系卖家")
        if row["expires_at"] and row["expires_at"] <= _utcnow().isoformat():
            raise ApiError("激活码已过期", status=403)
        # 会话校验：被踢下线（sid 不在表里，因他人在别处登录挤掉了）则要求重新登录
        sid = session.get("sid")
        if sid:
            s = db().execute(
                "SELECT sid FROM web_sessions WHERE license_id=? AND sid=?", (license_id, sid)
            ).fetchone()
            if not s:
                session.clear()
                raise ApiError("该激活码已在其他设备登录", status=401,
                               hint="同时在线数已达上限，请重新登录")
            db().execute("UPDATE web_sessions SET last_seen=? WHERE license_id=? AND sid=?",
                         (_utcnow().isoformat(), license_id, sid))
            db().commit()
        g.lid = license_id
        return row

    def _usage(row) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        u = db().execute(
            "SELECT count FROM usage WHERE license_id=? AND day=?", (row["id"], today)
        ).fetchone()
        return {"today_used": u["count"] if u else 0, "daily_quota": row["daily_quota"]}

    # ---------------- SESSDATA 加密存储 ----------------

    def _keystream(seed: str, n: int) -> bytes:
        out, counter = b"", 0
        while len(out) < n:
            out += hashlib.sha256(f"{seed}:{counter}".encode()).digest()
            counter += 1
        return out[:n]

    def _enc(text: str, lid: int) -> str:
        data = text.encode()
        key = _keystream(f"{app.config['SERVER_SECRET']}:secret:{lid}", len(data))
        return bytes(a ^ b for a, b in zip(data, key)).hex()

    def _dec(hex_text: str, lid: int) -> str:
        data = bytes.fromhex(hex_text)
        key = _keystream(f"{app.config['SERVER_SECRET']}:secret:{lid}", len(data))
        return bytes(a ^ b for a, b in zip(data, key)).decode("utf-8", errors="replace")

    def _sessdata(lid: int) -> str:
        if "sessdata" not in g:
            r = db().execute(
                "SELECT sessdata_enc FROM user_secrets WHERE license_id=?", (lid,)
            ).fetchone()
            g.sessdata = _dec(r["sessdata_enc"], lid) if r and r["sessdata_enc"] else ""
        return g.sessdata

    def _api_cfg(lid: int) -> dict | None:
        """读买家配置的 AI 提供商 + key；未配置返回 None。"""
        r = db().execute(
            "SELECT provider, api_key_enc FROM user_secrets WHERE license_id=?", (lid,)
        ).fetchone()
        if not r or not r["provider"] or not r["api_key_enc"]:
            return None
        p = PROVIDERS.get(r["provider"])
        if not p:
            return None
        return {"provider": r["provider"], "label": p["label"], "base_url": p["base_url"],
                "model": p["model"], "api_key": _dec(r["api_key_enc"], lid)}

    # ---------------- 登录 / 登出 ----------------

    @app.post("/api/login")
    def login():
        data = request.get_json(silent=True) or {}
        code = str(data.get("code") or "").strip()
        if not code:
            return _err("请输入激活码")
        try:
            resp = httpx.post(app.config["LICENSE_SERVER_URL"] + "/api/web/login",
                              json={"code": code}, timeout=15)
        except httpx.HTTPError as e:
            return _err(f"连不上授权服务器（{e.__class__.__name__}）", 502)
        try:
            d = resp.json()
        except ValueError:
            d = {}
        if resp.status_code != 200:
            return _err(d.get("error", f"登录失败（HTTP {resp.status_code}）"),
                        resp.status_code if resp.status_code >= 400 else 400, d.get("hint"))
        session.clear()
        session["lid"] = d["license_id"]
        session["token"] = d["token"]
        session.permanent = True
        # 会话限制：一码最多 MAX_WEB_SESSIONS 个活跃会话，超过踢最老（防共享）
        sid = secrets.token_hex(16)
        session["sid"] = sid
        lid = d["license_id"]
        now = _utcnow().isoformat()
        cutoff = (_utcnow() - timedelta(days=30)).isoformat()
        db().execute("DELETE FROM web_sessions WHERE license_id=? AND last_seen < ?", (lid, cutoff))
        old = db().execute(
            "SELECT sid FROM web_sessions WHERE license_id=? ORDER BY created_at DESC", (lid,)
        ).fetchall()
        for r in old[MAX_WEB_SESSIONS - 1:]:  # 新会话占 1 个，只留最新的 N-1 个旧的
            db().execute("DELETE FROM web_sessions WHERE license_id=? AND sid=?", (lid, r["sid"]))
        db().execute(
            "INSERT INTO web_sessions (license_id, sid, created_at, last_seen) VALUES (?,?,?,?)",
            (lid, sid, now, now),
        )
        db().commit()
        return jsonify({"success": True, "usage": d.get("usage")})

    @app.post("/api/logout")
    def logout():
        sid, lid = session.get("sid"), session.get("lid")
        if sid and lid:
            db().execute("DELETE FROM web_sessions WHERE license_id=? AND sid=?", (lid, sid))
            db().commit()
        session.clear()
        return jsonify({"success": True})

    # ---------------- 状态 / 配置（与本地工作台同形） ----------------

    @app.get("/api/status")
    def api_status():
        row = _license()
        api = _api_cfg(row["id"])
        return {
            "config_path": "网页版：配置存服务端（按激活码）",
            "sessdata_configured": bool(_sessdata(row["id"])),
            "glm_key_configured": bool(api),
            "provider": (api["label"] if api else "") or "",
            "model": (api["model"] if api else "") or "",
            "base_url": "",
            "endpoint": "openai",
            "hosted": True,
            "usage": _usage(row),
        }

    @app.get("/api/license/state")
    def api_license_state():
        row = _license()
        # 与桌面版同形，让状态卡直接显示配额；已过登录门，这里恒为已激活
        return {"server": "hosted", "activated": True, "online": True,
                "reason": "", "usage": _usage(row), "fingerprint": ""}

    @app.get("/api/config/get")
    def api_config_get():
        row = _license()
        configured = bool(_sessdata(row["id"]))
        api = _api_cfg(row["id"])
        return {
            "sessdata_configured": configured,
            "sessdata_hint": "" if configured else "未配置（可选，填了能解锁 AI 字幕）",
            "provider": api["provider"] if api else "",
            "api_key_configured": bool(api),
            "managed_server": "",
            "model": api["model"] if api else "",
            "glm_configured": bool(api),
            "config_path": "服务端（按激活码加密存储）",
        }

    @app.post("/api/config/save")
    def api_config_save():
        row = _license()
        data = request.get_json(silent=True) or {}
        lid = row["id"]
        if not any(k in data for k in ("sessdata", "provider", "api_key")):
            return _err("没有要保存的字段")
        r = db().execute(
            "SELECT sessdata_enc, provider, api_key_enc FROM user_secrets WHERE license_id=?", (lid,)
        ).fetchone()
        cur = {
            "sessdata": _dec(r["sessdata_enc"], lid) if r and r["sessdata_enc"] else "",
            "provider": r["provider"] if r else "",
            "api_key": _dec(r["api_key_enc"], lid) if r and r["api_key_enc"] else "",
        }
        if "sessdata" in data:
            cur["sessdata"] = str(data.get("sessdata") or "").strip()
        if "provider" in data:
            p = str(data.get("provider") or "").strip()
            if p and p not in PROVIDERS:
                return _err(f"未知提供商：{p}")
            cur["provider"] = p
        if "api_key" in data:
            cur["api_key"] = str(data.get("api_key") or "").strip()
        db().execute(
            "INSERT INTO user_secrets (license_id, sessdata_enc, provider, api_key_enc, updated_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT(license_id) DO UPDATE SET "
            "sessdata_enc=excluded.sessdata_enc, provider=excluded.provider, "
            "api_key_enc=excluded.api_key_enc, updated_at=excluded.updated_at",
            (lid, _enc(cur["sessdata"], lid) if cur["sessdata"] else None,
             cur["provider"] or None, _enc(cur["api_key"], lid) if cur["api_key"] else None,
             _utcnow().isoformat()),
        )
        db().commit()
        return api_config_get()

    # ---------------- 自定义模板（每码独立） ----------------

    def _prompts(lid: int) -> list[dict]:
        return [dict(r) for r in db().execute(
            "SELECT id, name, prompt FROM prompts WHERE license_id=? ORDER BY rowid", (lid,)
        )]

    @app.get("/api/prompts")
    def api_prompts():
        row = _license()
        return {"prompts": _prompts(row["id"])}

    @app.post("/api/prompts")
    def api_prompts_upsert():
        row = _license()
        data = request.get_json(silent=True) or {}
        name = str(data.get("name") or "").strip()
        prompt = str(data.get("prompt") or "").strip()
        if not name or not prompt:
            return _err("模板需要 name 和 prompt 两个字段")
        lid, pid = row["id"], data.get("id")
        if pid:
            cur = db().execute(
                "UPDATE prompts SET name=?, prompt=? WHERE license_id=? AND id=?",
                (name, prompt, lid, pid),
            )
            if cur.rowcount == 0:
                return _err(f"模板不存在：{pid}", 404)
        else:
            pid = f"p{int(time.time() * 1000):x}"
            db().execute("INSERT INTO prompts (license_id, id, name, prompt) VALUES (?,?,?,?)",
                         (lid, pid, name, prompt))
        db().commit()
        return {"id": pid, "name": name, "prompt": prompt}

    @app.delete("/api/prompts/<pid>")
    def api_prompts_delete(pid):
        row = _license()
        cur = db().execute("DELETE FROM prompts WHERE license_id=? AND id=?", (row["id"], pid))
        db().commit()
        if cur.rowcount == 0:
            return _err(f"模板不存在：{pid}", 404)
        return {"deleted": pid}

    # ---------------- 视频 / 字幕 / 总结（移植自本地 web.py） ----------------

    _CACHE: dict = {}            # (lid, bvid) → entry，进程内缓存
    _CACHE_LOCK = threading.Lock()

    def _client():
        return bilibili.make_client(_sessdata(g.lid))

    def _entry(url: str, page) -> dict:
        bvid = bilibili.parse_bvid(url)
        key = (g.lid, bvid)
        with _CACHE_LOCK:
            entry = _CACHE.get(key)
        if not entry:
            entry = {"bvid": bvid, "info": bilibili.get_video_info(_client(), bvid)}
            with _CACHE_LOCK:
                if len(_CACHE) > 400:
                    _CACHE.clear()
                _CACHE[key] = entry
        info = entry["info"]
        page = page or bilibili.parse_page(url) or 1
        pages = info.get("pages") or []
        if pages:
            if not 1 <= page <= len(pages):
                raise ApiError(f"视频只有 {len(pages)} 个分 P，第 {page} P 超出范围")
            p = pages[page - 1]
            cid, part = p["cid"], p.get("part") or info.get("title", "")
        else:
            cid, part = info["cid"], info.get("title", "")
        entry.update(page=page, cid=cid, part=part)
        return entry

    def _transcript(entry: dict) -> dict:
        cached = entry.setdefault("transcripts", {}).get(entry["cid"])
        if cached:
            return cached
        client = _client()
        duration = entry["info"].get("duration") or 0
        sub, lines, cov, consistent = bilibili.fetch_full_subtitle(
            client, entry["bvid"], entry["cid"], duration
        )
        if sub is None:
            if not _sessdata(g.lid):
                raise ApiError("拿不到字幕：请先在右上角设置里填写 B 站 SESSDATA",
                               hint="浏览器登录 B 站 → F12 → 应用 → Cookie → 复制 SESSDATA")
            if not bilibili.is_logged_in(client):
                raise ApiError("拿不到字幕：SESSDATA 已失效", hint="到设置面板更新 SESSDATA（约一个月过期）")
            raise ApiError("该视频没有可用字幕", hint="可尝试「元数据+热评」降级总结")
        if not lines:
            raise ApiError("字幕文件内容为空")
        text = subtitle.build_transcript(lines)
        t = {"lan": sub.get("lan") or "", "lan_doc": sub.get("lan_doc") or sub.get("lan") or "",
             "lines": len(lines), "chars": len(text), "coverage": cov,
             "consistent": consistent, "text": text}
        entry["transcripts"][entry["cid"]] = t
        return t

    def _meta_ctx(entry: dict) -> str:
        if "meta" in entry:
            return entry["meta"]
        client = _client()
        info = entry["info"]
        tags = bilibili.get_tags(client, entry["bvid"])
        comments = bilibili.get_hot_comments(client, info["aid"])
        entry["meta"] = meta.build_meta_context(info, tags, comments)
        return entry["meta"]

    def _conclusion(entry: dict) -> str | None:
        """无字幕兜底①：B 站官方 AI 总结（需登录；未登录返回 None 走元数据降级）。"""
        info = entry["info"]
        up_mid = (info.get("owner") or {}).get("mid")
        r = bilibili.get_conclusion(_client(), entry["bvid"], entry["cid"], up_mid)
        return summarizer.bili_conclusion_markdown(*r) if r else None

    class _Cfg:
        """AI 配置：买家配了 key 用买家直连；没配走服务器免费模型。"""
        def __init__(self, api: dict | None):
            if api:
                self.glm_api_key = api["api_key"]
                self.glm_base_url = api["base_url"]
                self.glm_model = api["model"]
            else:
                # 没配 key → 走服务器代理（服务器用免费模型 glm-4.7-flash）
                self.managed_server = app.config["LICENSE_SERVER_URL"]
                self.managed_auth = {"Authorization": f"Bearer {session['token']}"}

    @app.post("/api/parse")
    def api_parse():
        _license()
        data = request.get_json(silent=True) or {}
        entry = _entry(str(data.get("url") or ""), data.get("page"))
        info = entry["info"]
        stat = info.get("stat") or {}
        return {
            "bvid": entry["bvid"], "aid": info.get("aid"), "title": info.get("title", ""),
            "part": entry["part"], "page": entry["page"],
            "pages": [{"page": p.get("page"), "part": p.get("part"), "duration": p.get("duration")}
                      for p in (info.get("pages") or [])],
            "owner": (info.get("owner") or {}).get("name", ""),
            "duration": info.get("duration", 0),
            "desc": str(info.get("desc") or "").strip(),
            "stats": {k: stat.get(k, 0) for k in ("view", "like", "coin", "danmaku", "favorite")},
            "tags": bilibili.get_tags(_client(), entry["bvid"]),
        }

    @app.post("/api/subtitle")
    def api_subtitle():
        _license()
        data = request.get_json(silent=True) or {}
        entry = _entry(str(data.get("url") or ""), data.get("page"))
        t = _transcript(entry)
        return {"bvid": entry["bvid"], "lan": t["lan_doc"], "lines": t["lines"],
                "chars": t["chars"], "coverage": t["coverage"], "consistent": t["consistent"],
                "transcript": t["text"]}

    @app.post("/api/summarize")
    def api_summarize():
        row = _license()
        data = request.get_json(silent=True) or {}
        mode = str(data.get("mode") or "standard")
        api = _api_cfg(row["id"])  # None = 买家没配 key，走服务器免费模型
        p = None
        if mode == "custom":
            p = next((x for x in _prompts(row["id"]) if x["id"] == data.get("prompt_id")), None)
            if not p:
                return _err(f"模板不存在：{data.get('prompt_id')}", 404)
        entry = _entry(str(data.get("url") or ""), data.get("page"))
        title = entry["info"].get("title", "")
        cfg = _Cfg(api)
        if mode == "meta":
            return {"mode": mode, "markdown": summarizer.summarize_meta(_meta_ctx(entry), title, cfg)}
        if mode == "custom":
            t = _transcript(entry)
            md = summarizer.summarize_custom(t["text"], title, cfg, p["prompt"])
            return {"mode": mode, "name": p["name"], "prompt_id": p["id"],
                    "lan": t["lan_doc"], "markdown": md}
        if mode not in ("standard", "detailed"):
            return _err(f"未知总结模式：{mode}")
        try:
            t = _transcript(entry)
        except ApiError:
            # 无字幕兜底：先试 B 站官方 AI 总结（免登录），再降级元数据+热评
            cc = _conclusion(entry)
            if cc:
                return {"mode": mode, "lan": "B站官方AI总结", "markdown": cc,
                        "mindmap": None, "fallback": "conclusion"}
            return {"mode": mode, "markdown": summarizer.summarize_meta(_meta_ctx(entry), title, cfg),
                    "mindmap": None, "fallback": "meta"}
        md = summarizer.summarize(t["text"], title, cfg, detailed=(mode == "detailed"))
        return {"mode": mode, "lan": t["lan_doc"], "markdown": md,
                "mindmap": summarizer.extract_mindmap(md)}

    @app.post("/api/meta")
    def api_meta():
        _license()
        data = request.get_json(silent=True) or {}
        entry = _entry(str(data.get("url") or ""), data.get("page"))
        return {"bvid": entry["bvid"], "context": _meta_ctx(entry)}

    # ---------------- 前端与错误映射 ----------------

    @app.get("/")
    def index():
        path = _CORE / "static" / "index.html"
        if not path.exists():
            return _err(f"前端文件缺失：{path}", 500)
        return path.read_bytes(), 200, {"Content-Type": "text/html; charset=utf-8"}

    @app.get("/static/<path:name>")
    def static_file(name):
        import mimetypes
        p = _CORE / "static" / name
        if not p.is_file():
            return _err("not found", 404)
        mt = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        return p.read_bytes(), 200, {"Content-Type": mt}

    @app.errorhandler(ApiError)
    def _on_api_error(e):
        return _err(str(e), e.status, e.hint)

    @app.errorhandler(bilibili.BiliError)
    def _on_bili_error(e):
        return _err(str(e), 400, getattr(e, "hint", None))

    @app.errorhandler(summarizer.SummarizeError)
    def _on_sum_error(e):
        return _err(str(e), 400, e.hint)

    @app.errorhandler(Exception)
    def _on_exc(e):
        if isinstance(e, HTTPException):
            return _err(e.description, e.code or 400)
        return _err(f"服务器内部错误：{e.__class__.__name__}: {e}", 500)

    return app


# 开发运行：python hosted.py
if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=7842)
