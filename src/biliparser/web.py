"""本地 Web 工作台：标准库 http.server + 单文件前端，复用 CLI 同一套模块。

启动：biliparse-web（或 python -m biliparser.web），默认 http://127.0.0.1:7842
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import bilibili, config, licensing, meta, subtitle, summarizer

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

def api_status(cfg) -> dict:
    return {
        "config_path": str(config.CONFIG_PATH),
        "sessdata_configured": bool(cfg.sessdata),
        "glm_key_configured": bool(cfg.glm_api_key),
        "model": cfg.glm_model,
        "base_url": cfg.glm_base_url,
        "endpoint": "anthropic" if summarizer._is_anthropic_endpoint(cfg) else "openai",
        "provider": ("自有 Key" if cfg.glm_api_key else "服务器模型（免费）"),
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
    """激活门与状态卡数据。server 为空 = 直连模式，前端不设门。"""
    state = {
        "server": cfg.managed_server,
        "activated": False,
        "online": False,
        "reason": "",
        "usage": None,
        "fingerprint": licensing.fingerprint()[:16] + "…",
    }
    if cfg.managed_server and licensing.load_credential():
        v = licensing.verify(cfg.managed_server)
        state.update(activated=v["ok"], online=v.get("online", False),
                     reason=v.get("reason", ""), usage=v.get("usage"))
    return state


def api_license_activate(data: dict, cfg) -> dict:
    server = str(data.get("server") or cfg.managed_server or "").strip()
    code = str(data.get("code") or "").strip()
    if not server or not code:
        raise ApiError("需要 server 和 code")
    licensing.activate(server, code)  # 失败抛 LicensingError → 统一转 4xx
    if server != cfg.managed_server:
        config.update_config({"managed.server_url": server})
        cfg.managed_server = server
    return api_license_state(cfg)


# 自有 Key 直连的提供商映射（与 hosted.py 保持一致；server = 走授权服务器免费模型）
PROVIDERS = {
    "zhipu": {"label": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4/",
              "model": "glm-4.7-flash"},   # 免费档，用户 key 也是零成本
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
                 "model": "deepseek-chat"},
}


def api_config_get(cfg) -> dict:
    provider = getattr(cfg, "glm_provider", "") or ("server" if not cfg.glm_api_key else "")
    return {
        "sessdata_configured": bool(cfg.sessdata),
        "sessdata_hint": "" if cfg.sessdata else "未配置（可选，填了能解锁 AI 字幕）",
        "managed_server": cfg.managed_server,
        "model": cfg.glm_model,
        "glm_configured": bool(cfg.glm_api_key),
        "api_key_configured": bool(cfg.glm_api_key),
        "provider": provider,
        "provider_label": ("服务器模型（免费）" if provider in ("", "server")
                           else PROVIDERS.get(provider, {}).get("label", provider)),
        "config_path": str(config.CONFIG_PATH),
    }


def api_config_save(data: dict, cfg) -> dict:
    """设置面板写回：sessdata / 授权服务器 / AI 提供商与自有 Key。

    provider=server 或留空 → 清除自有 Key，回到服务器免费模型；
    provider=zhipu/deepseek → 写入对应 base_url/model，api_key 留空表示保持不变。
    """
    updates: dict = {}
    if "sessdata" in data:
        updates["sessdata"] = str(data.get("sessdata") or "").strip()
    if "managed_server" in data:
        updates["managed.server_url"] = str(data.get("managed_server") or "").strip()
    if "provider" in data:
        provider = str(data.get("provider") or "").strip()
        if provider in ("", "server"):
            updates["glm.api_key"] = ""
            updates["glm.provider"] = "server"
        elif provider in PROVIDERS:
            api_key = str(data.get("api_key") or "").strip()
            if api_key and not api_key.startswith("（"):
                updates["glm.api_key"] = api_key
            updates["glm.provider"] = provider
            updates["glm.base_url"] = PROVIDERS[provider]["base_url"]
            updates["glm.model"] = PROVIDERS[provider]["model"]
    elif "api_key" in data:
        # 只提交了 key 没动 provider：按当前 provider 存
        api_key = str(data.get("api_key") or "").strip()
        if api_key and not api_key.startswith("（"):
            updates["glm.api_key"] = api_key
    if not updates:
        raise ApiError("没有要保存的字段")
    config.update_config(updates)
    fresh = config.load_config(require=())
    cfg.sessdata = fresh.sessdata
    cfg.managed_server = fresh.managed_server
    cfg.glm_api_key = fresh.glm_api_key
    cfg.glm_model = fresh.glm_model
    cfg.glm_base_url = fresh.glm_base_url
    cfg.glm_provider = fresh.glm_provider
    return api_config_get(cfg)


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
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
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


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="biliparse-web", description="BiliParser 本地 Web 工作台")
    parser.add_argument("--port", type=int, default=7842)
    args = parser.parse_args(argv)

    cfg = config.load_config()  # 允许缺配置：页面能打开，状态区会提示缺什么
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
