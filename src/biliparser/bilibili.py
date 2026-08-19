"""B 站 web API 封装（仅字幕链路所需的三个请求）。

背景：bilibili-api-python 已于 2026-01 停止维护，这里用裸 HTTP 自实现。
接口可用性于 2026-08-18 实测验证。
"""

import hashlib
import json
import re
import time
from pathlib import Path

import httpx

from . import subtitle, wbi

API_BASE = "https://api.bilibili.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 完整浏览器 headers。2026-08 实测：view 接口只带 UA+Referer 会被风控拦截
# （HTTP 412），补齐 Accept/Origin/sec-ch-ua/Sec-Fetch-* 后恢复——无需任何指纹
# cookie。popular 等接口则宽松得多。
BROWSER_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "sec-ch-ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

# 业务错误码 → 用户可读提示
ERROR_MESSAGES = {
    -400: "请求错误（参数不合法）",
    -403: "访问被拒绝（权限不足）",
    -404: "视频不存在或已删除",
    62002: "稿件不可见（可能被 UP 主隐藏）",
    62004: "稿件审核中",
    62012: "仅 UP 主自己可见",
}


class BiliError(Exception):
    """B 站接口相关错误。hint 为给用户的解决建议。"""

    def __init__(self, message: str, *, code: int | None = None, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.hint = hint


_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_AV_RE = re.compile(r"\bav(\d+)", re.IGNORECASE)
_PAGE_RE = re.compile(r"[?&]p=(\d+)")


def parse_bvid(text: str) -> str:
    """从用户输入（BV 号或视频 URL）中解析 BV 号。"""
    text = text.strip()
    if "b23.tv" in text or "bili2233.cn" in text:
        raise BiliError(
            "暂不支持 B23 短链",
            hint="请先在浏览器打开短链，再复制地址栏里含 BV 号的完整链接",
        )
    m = _BV_RE.search(text)
    if m:
        return m.group(0)
    m = _AV_RE.search(text)
    if m:
        raise BiliError(
            f"暂不支持 av 号（{m.group(0)}）",
            hint="请在视频页面复制含 BV 号的完整链接",
        )
    raise BiliError(
        f"无法从输入中解析出 BV 号：{text!r}",
        hint="示例输入：BV1GJ411x7h7 或 https://www.bilibili.com/video/BV1GJ411x7h7",
    )


def parse_page(text: str) -> int | None:
    """从 URL 里取 ?p=N 分 P 参数（如 …?p=2），没有则 None。"""
    m = _PAGE_RE.search(text)
    return int(m.group(1)) if m else None


def make_client(sessdata: str) -> httpx.Client:
    cookies = {"SESSDATA": sessdata} if sessdata else {}
    return httpx.Client(
        base_url=API_BASE,
        headers=BROWSER_HEADERS,
        cookies=cookies,
        timeout=15,
        follow_redirects=True,
    )


def _request_json(client: httpx.Client, path: str, params: dict, what: str) -> dict:
    try:
        resp = client.get(path, params=params)
    except httpx.HTTPError as e:
        raise BiliError(f"{what}失败：网络错误（{e.__class__.__name__}）") from e
    if resp.status_code == 412:
        raise BiliError(
            f"{what}失败：被 B 站风控拦截（HTTP 412）", code=412, hint="请求过于频繁，请稍后再试"
        )
    if resp.status_code != 200:
        raise BiliError(f"{what}失败：HTTP {resp.status_code}", code=resp.status_code)
    return resp.json()


def _check(resp_json: dict, what: str) -> dict:
    code = resp_json.get("code", 0)
    if code != 0:
        msg = ERROR_MESSAGES.get(code, resp_json.get("message") or f"错误码 {code}")
        raise BiliError(f"{what}失败：{msg}", code=code)
    return resp_json.get("data", {})


def get_video_info(client: httpx.Client, bvid: str) -> dict:
    """视频信息：aid/cid/标题/UP 主/时长/分 P 列表。无需登录。"""
    data = _check(_request_json(client, "/x/web-interface/view", {"bvid": bvid}, "获取视频信息"), "获取视频信息")
    return data


def get_tags(client: httpx.Client, bvid: str) -> list[str]:
    """视频标签列表。无需登录。"""
    data = _check(_request_json(client, "/x/tag/archive/tags", {"bvid": bvid}, "获取视频标签"), "获取视频标签")
    return [str(t["tag_name"]) for t in data if t.get("tag_name")]


def get_hot_comments(client: httpx.Client, aid: int, limit: int = 20) -> list[dict]:
    """热门评论（按点赞排序，含置顶与少量楼中楼）。无需登录。

    返回 [{"message", "like", "pinned", "sub": [str, ...]}, ...]。
    未登录时 B 站只给约 20 条，够降级总结用。
    """
    data = _check(
        _request_json(
            client, "/x/v2/reply/main", {"type": 1, "oid": aid, "mode": 3}, "获取热门评论"
        ),
        "获取热门评论",
    )
    replies = list(data.get("replies") or [])

    # UP 置顶评论往往交代背景，放最前
    pinned = (data.get("upper") or {}).get("top")
    if pinned and pinned.get("content"):
        replies = [pinned] + [r for r in replies if r is not pinned]

    out = []
    for r in replies[:limit]:
        msg = (r.get("content") or {}).get("message", "")
        if not msg:
            continue
        out.append(
            {
                "message": msg,
                "like": r.get("like", 0),
                "pinned": r is pinned,
                "sub": [
                    (s.get("content") or {}).get("message", "")
                    for s in (r.get("replies") or [])[:2]
                ],
            }
        )
    return out


def _get_wbi_keys(client: httpx.Client) -> tuple[str, str]:
    data = _request_json(client, "/x/web-interface/nav", {}, "获取 wbi 密钥")
    return wbi.extract_keys(data["data"]["wbi_img"])


def is_logged_in(client: httpx.Client) -> bool:
    """通过 nav 接口检查 SESSDATA 登录态是否有效。

    未配置 / 已过期时 nav 返回 code -101 且 data.isLogin 为 false。
    用于字幕列表为空时区分「没登录」（AI 字幕不展示）和「视频无字幕」。
    """
    resp = _request_json(client, "/x/web-interface/nav", {}, "检查登录状态")
    return bool(resp.get("data", {}).get("isLogin"))


def get_subtitle_info(client: httpx.Client, bvid: str, cid: int, attempts: int = 4) -> dict:
    """返回 player 接口的 subtitle 子对象 {"subtitles": [...], ...}。

    2026-08 实测两个坑：
    1. wbi/v2 签名端点对本工具的请求 subtitles 恒为空（疑似按指纹降级）；
    2. /x/player/v2 多机返回不一致——同一请求约 1/3 概率非空（网页播放器
       靠重试拿到），bvid 与 aid 参数命中率相近。
    故按 [wbi 签名 / 不签名] × attempts 轮重试直到非空；全空时返回最后的
    空对象，由调用方结合 is_logged_in() 区分「没登录」和「没有字幕」。
    """
    result = {}
    img_key = sub_key = None
    for attempt in range(attempts):
        for path, sign in (("/x/player/wbi/v2", True), ("/x/player/v2", False)):
            try:
                params = {"bvid": bvid, "cid": cid}
                if sign:
                    if img_key is None:
                        img_key, sub_key = _get_wbi_keys(client)
                    params = wbi.sign_params(params, img_key, sub_key)
                data = _request_json(client, path, params, "获取字幕列表")
                if data.get("code") == 0:
                    result = (data.get("data") or {}).get("subtitle", {}) or {}
                    if result.get("subtitles"):
                        return result
            except (BiliError, KeyError, ValueError):
                continue
        time.sleep(0.2)  # 多机不一致，稍候换台机器再试
    return result


def download_subtitle(client: httpx.Client, subtitle_url: str) -> list[dict]:
    """下载字幕 JSON，返回 body 行列表 [{from, to, content}, ...]。

    subtitle_url 是协议相对地址（//aisubtitle.h5.cn/... 或 //i0.hdslb.com/...），
    且不在 api.bilibili.com 域下，需单独请求。
    """
    url = subtitle_url if subtitle_url.startswith("https:") else "https:" + subtitle_url
    try:
        resp = client.get(url)  # httpx 对绝对 URL 忽略 base_url
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise BiliError(f"下载字幕失败：{e.__class__.__name__}") from e
    return resp.json().get("body") or []


# ---------------- 跨视频重复字幕检测（串台实锤手段） ----------------
# 实测（2026-08）：同一份错误字幕文件会出现在多个不同视频下（「弟弟读大学」
# 字幕同时挂在乡村 vlog 和西游记解读两个视频上）。语义校验对同题材串台
# 无能为力，但「同一字幕文本出现在两个 cid 下」是确定性矛盾——记录指纹，
# 命中即拒。副作用：搬运/重传的视频（内容相同）会误报，错误信息会提示。

_SEEN_PATH = Path.home() / ".biliparser" / "seen_subs.json"


def _load_seen() -> dict:
    try:
        data = json.loads(_SEEN_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_seen(seen: dict) -> None:
    try:
        _SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SEEN_PATH.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def subtitle_fingerprint(lines: list[dict]) -> str:
    """字幕内容指纹：首行 + 行数 + 总字符量。足以识别同一文件的重复分发。"""
    first = next((str(l.get("content", "")).strip() for l in lines if str(l.get("content", "")).strip()), "")
    total = sum(len(str(l.get("content", ""))) for l in lines)
    return hashlib.sha256(f"{first}|{len(lines)}|{total}".encode()).hexdigest()[:16]


def get_audio_url(client: httpx.Client, bvid: str, cid: int) -> str:
    """取音频流地址（DASH 最低码率那条），供本地 ASR 兜底使用。

    需要 SESSDATA 登录态才能拿到 DASH。ASR 场景人声用低码率足够。
    """
    data = _check(
        _request_json(
            client, "/x/player/playurl",
            {"bvid": bvid, "cid": cid, "fnval": 16, "qn": 0}, "获取音频流",
        ),
        "获取音频流",
    )
    audio = ((data.get("dash") or {}).get("audio")) or []
    if not audio:
        raise BiliError(
            "拿不到音频流（DASH）",
            hint="未登录（SESSDATA）或该视频不允许下载；ASR 兜底需要登录态",
        )
    return min(audio, key=lambda a: a.get("bandwidth") or 10**9)["baseUrl"]


def fetch_full_subtitle(
    client: httpx.Client, bvid: str, cid: int, duration: int = 0,
    min_coverage: float = 0.8, rounds: int = 4, lang: str | None = None,
) -> tuple[dict | None, list[dict], float]:
    """挑「最完整」的一条字幕，返回 (sub, lines, coverage)。

    2026-08 实测的坑，都在这里兜住：
    1. 字幕列表多机返回不一致 → get_subtitle_info 内部已重试；
    2. UP 上传的 CC 可能是残缺占位（实例：5 分钟视频只有 1 行 / 27 秒），
       而同名 AI 字幕反而完整——不能只按语言优先级挑，要下载后按覆盖
       时长比较，同分才按语言优先级（prioritize 顺序 + 严格大于）；
    3. AI 字幕文件在 CDN 各节点版本不一（实测同视频新签名 URL 随机返回
       1%~79% 的版本，疑似渐进生成/同步）——每轮重新取列表（新 URL）、
       下载，跨轮保留最佳，直到覆盖率 ≥ min_coverage 或打满 rounds 轮。
    4. 新发布视频的 AI 字幕有「串台期」：CDN 会返回完全不属于本视频的
       字幕文件（实例 BV1HZgV6TEGm，标题是西游记解读，拉到的字幕分别
       是 LoL 比赛解说和麦当劳复刻）。同一视频的合法版本首行应一致
       （渐进生成只增不改），因此用「各次下载的首行指纹」做一致性检测，
       不一致时 consistent=False，由调用方警告用户。

    返回 (sub, lines, coverage, consistent)；
    视频无字幕（或指定 lang 无匹配）返回 (None, [], 0, True)；
    duration 未知时按行数比较、coverage 记 1.0。
    """
    best_sub, best_lines, best_cov = None, [], 0.0
    fingerprints: set[str] = set()
    seen = _load_seen()
    for _ in range(rounds):
        subs = get_subtitle_info(client, bvid, cid).get("subtitles") or []
        if lang:
            subs = [s for s in subs if s.get("lan") == lang]
        if not subs:
            break
        for sub in subtitle.prioritize(subs):
            try:
                lines = download_subtitle(client, sub["subtitle_url"])
            except BiliError:
                continue
            if not lines:
                continue
            first = next((str(l.get("content", "")).strip() for l in lines if str(l.get("content", "")).strip()), "")
            if first:
                fingerprints.add(first)
            # 跨视频重复：同一字幕文件挂在别的视频下 = 串台实锤，跳过
            owner = seen.get(subtitle_fingerprint(lines))
            if owner is not None and owner != cid:
                continue
            if duration:
                max_to = max(l.get("to") or 0 for l in lines)
                # 字幕比视频还长（留 5% 容差）：数学上不可能，串台实锤
                if max_to > duration * 1.05:
                    continue
                cov = min(max_to / duration, 1.0)
            else:
                cov = 1.0
            score = cov if duration else float(len(lines))
            if score > (best_cov if duration else len(best_lines)):
                best_sub, best_lines, best_cov = sub, lines, cov
        if best_lines and (not duration or best_cov >= min_coverage):
            break
        time.sleep(0.05)
    if best_lines:
        seen[subtitle_fingerprint(best_lines)] = cid
        _save_seen(seen)
    return best_sub, best_lines, (best_cov if duration else 1.0), len(fingerprints) <= 1
