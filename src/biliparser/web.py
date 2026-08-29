"""本地 Web 工作台：标准库 http.server + 单文件前端，复用 CLI 同一套模块。

启动：biliparse-web（或 python -m biliparser.web），默认 http://127.0.0.1:7842
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from . import __version__, bilibili, config, licensing, meta, subtitle, summarizer

def _resolve_static_dir() -> Path:
    """静态页面目录：源码运行在包目录下；PyInstaller frozen 时在
    sys._MEIPASS（Contents/Frameworks）下，两处都找。"""
    here = Path(__file__).parent / "static"
    if here.exists():
        return here
    import sys

    base = getattr(sys, "_MEIPASS", None)
    if base:
        alt = Path(base) / "biliparser" / "static"
        if alt.exists():
            return alt
    return here


STATIC_DIR = _resolve_static_dir()
PROMPTS_PATH = Path.home() / ".biliparser" / "prompts.json"

# 进程内缓存：bvid → {"info": …, "pages": …, "transcript": …, "meta": …}
_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()


class ApiError(Exception):
    def __init__(self, message: str, hint: str | None = None, status: int = 400):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.status = status


def _client(cfg):
    return bilibili.make_client(cfg.sessdata)


def _page_info(info: dict, page: int) -> tuple[int, str]:
    """返回 (cid, part)。分 P 越界时抛 ApiError。"""
    pages = info.get("pages") or []
    if pages:
        if not 1 <= page <= len(pages):
            raise ApiError(f"视频只有 {len(pages)} 个分 P，第 {page} P 超出范围")
        p = pages[page - 1]
        return p["cid"], p.get("part") or info.get("title", "")
    return info["cid"], info.get("title", "")


def _get_video(url: str, page: int | None, cfg) -> dict:
    """解析 URL → 缓存的 {info, page, cid, part}；已缓存则直接复用。"""
    bvid = bilibili.parse_bvid(url)
    with _CACHE_LOCK:
        entry = _CACHE.get(bvid)
    if not entry:
        info = bilibili.get_video_info(_client(cfg), bvid)
        entry = {"bvid": bvid, "info": info}
        with _CACHE_LOCK:
            _CACHE[bvid] = entry
    info = entry["info"]
    page = page or bilibili.parse_page(url) or 1  # 链接里的 ?p=N 也认
    cid, part = _page_info(info, page)
    entry.update(page=page, cid=cid, part=part)
    return entry


def _get_transcript(entry: dict, cfg) -> dict:
    """带缓存的字幕获取（按 cid 缓存，支持多 P）；无字幕时抛 ApiError（附降级提示）。"""
    cached = entry.setdefault("transcripts", {}).get(entry["cid"])
    if cached:
        return cached
    client = _client(cfg)
    duration = entry["info"].get("duration") or 0
    sub, lines, cov, consistent = bilibili.fetch_full_subtitle(
        client, entry["bvid"], entry["cid"], duration
    )
    if sub is None:
        if not bilibili.is_logged_in(client):
            raise ApiError(
                "拿不到字幕：SESSDATA 未配置或已失效",
                hint="浏览器登录 B 站后复制 SESSDATA 填入 ~/.biliparser/config.toml；"
                "或先用右侧「元数据+热评」降级模式",
            )
        raise ApiError(
            "该视频没有可用字幕",
            hint="可尝试「元数据+热评」降级总结（推断性结果）",
        )
    if not lines:
        raise ApiError("字幕文件内容为空")
    text = subtitle.build_transcript(lines)
    transcript = {
        "lan": sub.get("lan") or "",
        "lan_doc": sub.get("lan_doc") or sub.get("lan") or "",
        "lines": len(lines),
        "chars": len(text),
        "coverage": cov,
        "consistent": consistent,
        "text": text,
    }
    entry["transcripts"][entry["cid"]] = transcript
    return transcript


def _get_meta(entry: dict, cfg) -> str:
    if "meta" in entry:
        return entry["meta"]
    client = _client(cfg)
    info = entry["info"]
    tags = bilibili.get_tags(client, entry["bvid"])
    comments = bilibili.get_hot_comments(client, info["aid"])
    entry["meta"] = meta.build_meta_context(info, tags, comments)
    return entry["meta"]


def _get_conclusion_markdown(entry: dict, cfg) -> str | None:
    """无字幕兜底①：B 站官方 AI 总结（需登录；未登录返回 None 走元数据降级）。"""
    info = entry["info"]
    up_mid = (info.get("owner") or {}).get("mid")
    r = bilibili.get_conclusion(_client(cfg), entry["bvid"], entry["cid"], up_mid)
    if not r:
        return None
    return summarizer.bili_conclusion_markdown(*r)


# ---------------- 自定义模板（~/.biliparser/prompts.json） ----------------

def load_prompts() -> list[dict]:
    """已保存的自定义模板 [{"id", "name", "prompt"}, ...]。文件损坏时静默当空。"""
    try:
        data = json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))
        return [p for p in data if isinstance(p, dict) and p.get("name") and p.get("prompt")]
    except (OSError, ValueError):
        return []


def save_prompts(prompts: list[dict]) -> None:
    PROMPTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROMPTS_PATH.write_text(json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_prompt(data: dict) -> dict:
    """新建或更新模板：带合法 id → 更新；否则新建。返回落盘后的模板。"""
    name = str(data.get("name") or "").strip()
    prompt = str(data.get("prompt") or "").strip()
    if not name or not prompt:
        raise ApiError("模板需要 name 和 prompt 两个字段")
    prompts = load_prompts()
    pid = data.get("id")
    if pid:
        for i, p in enumerate(prompts):
            if p.get("id") == pid:
                prompts[i] = {"id": pid, "name": name, "prompt": prompt}
                save_prompts(prompts)
                return prompts[i]
    item = {"id": f"p{int(time.time() * 1000):x}", "name": name, "prompt": prompt}
    prompts.append(item)
    save_prompts(prompts)
    return item


def delete_prompt(pid: str) -> dict:
    prompts = load_prompts()
    rest = [p for p in prompts if p.get("id") != pid]
    if len(rest) == len(prompts):
        raise ApiError(f"模板不存在：{pid}", status=404)
    save_prompts(rest)
    return {"deleted": pid}


# ---------------- API 动作（纯函数风格，方便测试与复用） ----------------

def _app_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version
        return version("biliparser")
    except Exception:
        return "dev"


def _detect_provider(key: str) -> str | None:
    """按 Key 格式自动识别提供商（设置面板免选）：
    sk- 开头 → DeepSeek；形如 id.secret（前段≥8位再带点）→ 智谱；识别不了 → None。"""
    k = (key or "").strip()
    if k.startswith("sk-"):
        return "deepseek"
    if "." in k and len(k.split(".", 1)[0]) >= 8:
        return "zhipu"
    return None


def _short(secret: str, head: int = 4, tail: int = 4) -> str:
    """状态卡缩略展示（如 f839…IIEC），不泄露全文；未配置返回空串。"""
    s = str(secret or "").strip()
    if not s:
        return ""
    return s if len(s) <= head + tail + 2 else f"{s[:head]}…{s[-tail:]}"


_VALID_CACHE: dict = {}   # kind -> (关联值, 结果)：值没变不重复联网校验


def _validate_sessdata(sessdata: str, _force: bool = False) -> bool | None:
    """实测 SESSDATA 登录态：True 有效 / False 失效 / None 联网失败（不武断判死）。"""
    if not sessdata:
        return None
    if not _force:
        cached = _VALID_CACHE.get("sessdata")
        if cached and cached[0] == sessdata:
            return cached[1]
    try:
        result = bool(bilibili.is_logged_in(bilibili.make_client(sessdata)))
    except Exception:
        result = None
    _VALID_CACHE["sessdata"] = (sessdata, result)
    return result


def _validate_api_key(cfg, _force: bool = False) -> bool | None:
    """实测 API Key（最小一次对话）：401/403 = 无效，其他失败 = 未知。"""
    if not cfg.glm_api_key:
        return None
    cache_key = (cfg.glm_api_key, cfg.glm_base_url)
    if not _force:
        cached = _VALID_CACHE.get("api_key")
        if cached and cached[0] == cache_key:
            return cached[1]
    try:
        summarizer._chat(cfg, [{"role": "user", "content": "ping"}])
        result = True
    except summarizer.SummarizeError as e:
        msg = str(e)
        result = False if ("401" in msg or "403" in msg or "402" in msg
                           or "Key 无效" in msg or "余额不足" in msg or "无可用资源包" in msg) else None
    except Exception:
        result = None
    _VALID_CACHE["api_key"] = (cache_key, result)
    return result


def api_update_check(cfg) -> dict:
    """启动更新提示：本地 __version__ 对比官网 version.json。

    服务器挂了 / 没有清单 = 静默不提示——更新检查永远不阻塞使用。
    """
    latest = ""
    try:
        r = httpx.get(licensing.OFFICIAL_SITE.rstrip("/") + "/download/version.json", timeout=5)
        latest = str((r.json() or {}).get("version") or "")
    except Exception:
        latest = ""
    return {"current": __version__, "latest": latest,
            "update_available": bool(latest) and latest != __version__}


def api_config_validate(cfg) -> dict:
    """状态卡真实校验：启动/刷新时前端异步调，结果按值缓存（同值不重复联网）。"""
    return {"sessdata_valid": _validate_sessdata(cfg.sessdata),
            "api_key_valid": _validate_api_key(cfg)}


def api_status(cfg) -> dict:
    return {
        "config_path": str(config.CONFIG_PATH),
        "sessdata_configured": bool(cfg.sessdata),
        "sessdata_short": _short(cfg.sessdata),
        "glm_key_configured": bool(cfg.glm_api_key),
        "api_key_short": _short(cfg.glm_api_key),
        "model": cfg.glm_model,
        "base_url": cfg.glm_base_url,
        "endpoint": "anthropic" if summarizer._is_anthropic_endpoint(cfg) else "openai",
        "provider": ("自有 Key" if cfg.glm_api_key else "未配置 API Key"),
        "version": _app_version(),
        "official_site": licensing.OFFICIAL_SITE,   # 品牌名点击跳转的官网（/open-official 使用）
    }


def api_parse(url: str, page: int | None, cfg) -> dict:
    entry = _get_video(url, page, cfg)
    info = entry["info"]
    stat = info.get("stat") or {}
    return {
        "bvid": entry["bvid"],
        "aid": info.get("aid"),
        "title": info.get("title", ""),
        "part": entry["part"],
        "page": entry["page"],
        "pages": [
            {"page": p.get("page"), "part": p.get("part"), "duration": p.get("duration")}
            for p in (info.get("pages") or [])
        ],
        "owner": (info.get("owner") or {}).get("name", ""),
        "duration": info.get("duration", 0),
        "desc": str(info.get("desc") or "").strip(),
        "stats": {k: stat.get(k, 0) for k in ("view", "like", "coin", "danmaku", "favorite")},
        "tags": bilibili.get_tags(_client(cfg), entry["bvid"]),
    }


def api_subtitle(url: str, page: int | None, cfg) -> dict:
    entry = _get_video(url, page, cfg)
    t = _get_transcript(entry, cfg)
    return {
        "bvid": entry["bvid"], "lan": t["lan_doc"], "lines": t["lines"],
        "chars": t["chars"], "coverage": t["coverage"], "consistent": t["consistent"],
        "transcript": t["text"],
    }


def _gate(cfg) -> None:
    """发行版激活门（后端强制）：不激活不能用。

    未激活 → 业务接口（解析/字幕/总结/模板/配置写）一律 403，只放行
    /api/license/*（查状态、输码）。直连自用版（未烧入服务器地址）不设门。
    """
    if not cfg.managed_server:
        return
    v = licensing.verify_local()
    if not v["ok"]:
        raise ApiError(
            "未激活，应用不可用", status=403,
            hint=v.get("reason") or "请先输码激活",
        )


def api_summarize(url: str, page: int | None, mode: str, cfg, prompt_id: str | None = None) -> dict:
    # 自定义模板存在性检查不依赖网络/配置，先做，保证 404 干净报错
    if mode == "custom":
        p = next((x for x in load_prompts() if x.get("id") == prompt_id), None)
        if not p:
            raise ApiError(f"模板不存在：{prompt_id}", status=404)
    entry = _get_video(url, page, cfg)
    title = entry["info"].get("title", "")
    if not cfg.glm_api_key and mode != "subtitle":
        raise config.ConfigError(
            "智谱 GLM API Key 未配置", hint=f"请填写 {config.CONFIG_PATH} 或设置 ZHIPUAI_API_KEY"
        )
    if mode == "meta":
        context = _get_meta(entry, cfg)
        return {"mode": mode, "markdown": summarizer.summarize_meta(context, title, cfg)}
    if mode == "custom":
        t = _get_transcript(entry, cfg)
        md = summarizer.summarize_custom(t["text"], title, cfg, p["prompt"])
        return {"mode": mode, "name": p["name"], "prompt_id": p["id"], "lan": t["lan_doc"], "markdown": md}
    if mode not in ("standard", "detailed"):
        raise ApiError(f"未知总结模式：{mode}")
    try:
        t = _get_transcript(entry, cfg)
    except ApiError:
        # 无字幕兜底：先试 B 站官方 AI 总结（免登录），再降级元数据+热评
        cc = _get_conclusion_markdown(entry, cfg)
        if cc:
            return {"mode": mode, "lan": "B站官方AI总结", "markdown": cc,
                    "mindmap": None, "fallback": "conclusion"}
        context = _get_meta(entry, cfg)
        return {"mode": mode, "markdown": summarizer.summarize_meta(context, title, cfg),
                "mindmap": None, "fallback": "meta"}
    md = summarizer.summarize(
        t["text"], title, cfg,
        detailed=(mode == "detailed"),
        include_mindmap=(mode == "detailed"),
    )
    return {"mode": mode, "lan": t["lan_doc"], "markdown": md,
            "mindmap": summarizer.extract_mindmap(md) if mode == "detailed" else None}


def api_meta(url: str, page: int | None, cfg) -> dict:
    entry = _get_video(url, page, cfg)
    return {"bvid": entry["bvid"], "context": _get_meta(entry, cfg)}


# ---------------- 授权 / 配置（发行版模式） ----------------

def api_license_state(cfg) -> dict:
    """状态卡数据：本地校验凭证（重算 HMAC + 核对 MAC），全程不联网。

    server 为空 = 直连模式（开发自用），前端不设门。
    """
    state = {
        "server": cfg.managed_server,
        "activated": False,
        "reason": "",
    }
    if not cfg.managed_server:
        return state
    v = licensing.verify_local()
    state.update(activated=v["ok"], reason=v.get("reason", ""))
    return state


def api_license_activate(data: dict, cfg) -> dict:
    """输码激活：唯一联网动作（POST /api/v1/license/activate），成功后凭证落盘。"""
    server = str(data.get("server") or cfg.managed_server or "").strip()
    code = str(data.get("code") or "").strip()
    if not server or not code:
        raise ApiError("需要 server 和 code")
    licensing.activate(server, code)  # 失败抛 LicensingError → 统一转 4xx
    if server != cfg.managed_server:
        config.update_config({"managed.server_url": server})
        cfg.managed_server = server
    return api_license_state(cfg)


# 自有 Key 直连的提供商映射（AI 费用买家自付，服务器不代理）
PROVIDERS = {
    "zhipu": {"label": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4/",
              "model": "glm-4.7-flash"},   # 免费档，用户 key 也是零成本
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
                 "model": "deepseek-chat"},
}


def _peek_valid(kind: str, cache_key) -> bool | None:
    """读校验缓存（值相同才命中）：不给 config/get 触发联网，已测过就透出真实结果。"""
    cached = _VALID_CACHE.get(kind)
    return cached[1] if cached and cached[0] == cache_key else None


def api_config_get(cfg) -> dict:
    provider = getattr(cfg, "glm_provider", "") or "zhipu"
    return {
        "sessdata_configured": bool(cfg.sessdata),
        "sessdata_hint": "" if cfg.sessdata else "未配置（可选，填了能解锁 AI 字幕）",
        "sessdata_value": cfg.sessdata or "",
        "sessdata_short": _short(cfg.sessdata),
        "sessdata_valid": _peek_valid("sessdata", cfg.sessdata),
        "managed_server": cfg.managed_server,
        "model": cfg.glm_model,
        "glm_configured": bool(cfg.glm_api_key),
        "glm_key_configured": bool(cfg.glm_api_key),
        "api_key_configured": bool(cfg.glm_api_key),
        "api_key_value": cfg.glm_api_key or "",
        "api_key_short": _short(cfg.glm_api_key),
        "api_key_valid": _peek_valid("api_key", (cfg.glm_api_key, cfg.glm_base_url)),
        "provider": provider,
        "provider_label": PROVIDERS.get(provider, {}).get("label", provider),
        "config_path": str(config.CONFIG_PATH),
    }


def api_config_save(data: dict, cfg) -> dict:
    """设置面板写回：sessdata / 授权服务器 / AI 提供商与自有 Key。

    面板直接回填真实值（8/25 拍板：不用「已配置，留空保持不变」占位），
    所以提交语义为「改动才提交、清空=删除」：字段提交空串即删除该配置。
    保存后实测 SESSDATA 登录态与 API Key，返回 *_valid（true/false/null）。
    """
    updates: dict = {}
    if "sessdata" in data:
        updates["sessdata"] = str(data.get("sessdata") or "").strip()
    if "managed_server" in data:
        updates["managed.server_url"] = str(data.get("managed_server") or "").strip()
    if "provider" in data:
        provider = str(data.get("provider") or "").strip()
        if provider not in PROVIDERS:
            raise ApiError("请选择 AI 提供商（智谱 / DeepSeek）")
        updates["glm.provider"] = provider
        updates["glm.base_url"] = PROVIDERS[provider]["base_url"]
        updates["glm.model"] = PROVIDERS[provider]["model"]
    if "api_key" in data:
        api_key = str(data.get("api_key") or "").strip()
        updates["glm.api_key"] = api_key
        detected = _detect_provider(api_key)      # 免选提供商：粘贴 Key 自动识别
        if detected:
            updates["glm.provider"] = detected
            updates["glm.base_url"] = PROVIDERS[detected]["base_url"]
            updates["glm.model"] = PROVIDERS[detected]["model"]
    if not updates:
        raise ApiError("没有要保存的字段")
    config.update_config(updates)
    fresh = config.load_config(require=())
    for attr in ("sessdata", "managed_server", "glm_api_key",
                 "glm_model", "glm_base_url", "glm_provider"):
        setattr(cfg, attr, getattr(fresh, attr))
    out = api_config_get(cfg)
    # 保存必重测（_force 绕过缓存）：充值/换 Key 后同值重存也能拿到新结果
    out["sessdata_valid"] = _validate_sessdata(cfg.sessdata, _force=True)
    out["api_key_valid"] = _validate_api_key(cfg, _force=True)
    return out


# ---------------- HTTP 层 ----------------

class Handler(BaseHTTPRequestHandler):
    cfg = None  # 由 serve() 注入

    def log_message(self, fmt, *args):  # 静默默认访问日志，出问题时手动开
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError("请求体不是合法 JSON")

    def _run(self, fn, *args):
        """统一执行：业务异常 → 4xx + {error, hint}；未预期异常 → 500。"""
        try:
            return self._send_json(fn(*args))
        except (ApiError, bilibili.BiliError, config.ConfigError,
                summarizer.SummarizeError, licensing.LicensingError) as e:
            return self._send_json(
                {"error": str(e), "hint": getattr(e, "hint", None) or getattr(e, "message", None)},
                status=getattr(e, "status", 400),
            )
        except Exception as e:  # noqa: BLE001
            return self._send_json({"error": f"服务器内部错误：{e.__class__.__name__}: {e}"}, status=500)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            page = STATIC_DIR / "index.html"
            if not page.exists():
                return self._send_json({"error": f"前端文件缺失：{page}"}, status=500)
            body = page.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/status":
            self._run(api_status, self.cfg)
        elif self.path == "/api/prompts":
            self._run(lambda: {"prompts": load_prompts()})
        elif self.path == "/api/license/state":
            self._run(api_license_state, self.cfg)
        elif self.path == "/api/config/get":
            self._run(api_config_get, self.cfg)
        elif self.path == "/api/config/validate":
            self._run(api_config_validate, self.cfg)
        elif self.path == "/api/update-check":
            self._run(api_update_check, self.cfg)
        elif self.path == "/activate.html":
            page = STATIC_DIR / "activate.html"
            if not page.exists():
                return self._send_json({"error": f"前端文件缺失：{page}"}, status=500)
            body = page.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/open-official":
            # pywebview 里 target=_blank 打不开外部链接：由本机服务调系统浏览器开官网
            import webbrowser
            try:
                webbrowser.open(licensing.OFFICIAL_SITE)
                opened = True
            except Exception:
                opened = False
            site = licensing.OFFICIAL_SITE
            body = (
                "<!DOCTYPE html><meta charset='utf-8'><body style='background:#101418;"
                "color:#dce4ee;font:14px/1.8 -apple-system,PingFang SC,sans-serif;"
                "display:flex;align-items:center;justify-content:center;min-height:100vh'>"
                + ("<div>✓ 已在系统浏览器打开官网，本页可关闭</div>" if opened
                   else f"<div>无法自动打开，请手动访问：<a style='color:#4da3ff' href='{site}'>{site}</a></div>")
                + "</body>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/static/"):
            name = self.path[len("/static/"):]
            f = (STATIC_DIR / name).resolve()
            if f.parent == STATIC_DIR.resolve() and f.is_file():
                body = f.read_bytes()
                ctype = f.suffix.lower() in (".png",) and "image/png" or \
                    f.suffix.lower() in (".jpg", ".jpeg") and "image/jpeg" or "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._send_json({"error": "not found"}, status=404)
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        # 激活门（集中）：未激活 = 不能用，业务接口全拦，仅激活相关放行
        if self.path not in ("/api/license/state", "/api/license/activate"):
            try:
                _gate(self.cfg)
            except ApiError as e:
                return self._send_json({"error": e.message, "hint": e.hint}, status=e.status)
        routes = {
            "/api/parse": lambda d: api_parse(d.get("url", ""), d.get("page"), self.cfg),
            "/api/subtitle": lambda d: api_subtitle(d.get("url", ""), d.get("page"), self.cfg),
            "/api/summarize": lambda d: api_summarize(
                d.get("url", ""), d.get("page"), d.get("mode", "standard"), self.cfg,
                prompt_id=d.get("prompt_id"),
            ),
            "/api/meta": lambda d: api_meta(d.get("url", ""), d.get("page"), self.cfg),
            "/api/prompts": lambda d: upsert_prompt(d),
            "/api/license/state": lambda d: api_license_state(self.cfg),
            "/api/license/activate": lambda d: api_license_activate(d, self.cfg),
            "/api/config/get": lambda d: api_config_get(self.cfg),
            "/api/config/save": lambda d: api_config_save(d, self.cfg),
        }
        fn = routes.get(self.path)
        if not fn:
            return self._send_json({"error": "not found"}, status=404)
        try:
            data = self._read_json()
        except ApiError as e:
            return self._send_json({"error": e.message}, status=400)
        self._run(fn, data)

    def do_DELETE(self):
        if self.path.startswith("/api/prompts/"):
            return self._run(delete_prompt, self.path[len("/api/prompts/"):])
        self._send_json({"error": "not found"}, status=404)


def make_server(cfg, port: int) -> ThreadingHTTPServer:
    Handler.cfg = cfg
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def _startup_verify(cfg) -> None:
    """发行版每次启动联网核验（2026-08-29 用户拍板：后台解绑要能踢掉老设备）。

    码被后台解绑/已换绑 → verify_remote 清凭证，激活门随即拦下；
    服务器不可达/老服务器无此接口 → 离线宽容放行（保住「离线也能用」）。
    直连自用版（未烧服务器地址）跳过。desktop.main 与 web.main 启动时各调一次。
    """
    if not cfg.managed_server:
        return
    if not licensing.verify_local()["ok"]:
        return                                   # 本来就没激活，直接走激活页
    r = licensing.verify_remote(cfg.managed_server)
    if r["checked"] and not r["ok"]:
        print(f"授权已失效：{r['reason']}，请重新激活", flush=True)
    elif not r["checked"] and r.get("reason"):
        print(f"启动核验：{r['reason']}", flush=True)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="biliparse-web", description="BiliParser 本地 Web 工作台")
    parser.add_argument("--port", type=int, default=7842)
    args = parser.parse_args(argv)

    cfg = config.load_config()  # 允许缺配置：页面能打开，状态区会提示缺什么
    _startup_verify(cfg)
    server = make_server(cfg, args.port)
    print(f"BiliParser 工作台：http://127.0.0.1:{args.port}")
    print("配置：", json.dumps(api_status(cfg), ensure_ascii=False))
    print("Ctrl+C 退出")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
