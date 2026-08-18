"""B 站 web API 封装（仅字幕链路所需的三个请求）。

背景：bilibili-api-python 已于 2026-01 停止维护，这里用裸 HTTP 自实现。
接口可用性于 2026-08-18 实测验证。
"""

import re

import httpx

from . import wbi

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


def get_subtitle_info(client: httpx.Client, bvid: str, cid: int) -> dict:
    """返回 player 接口的 subtitle 子对象 {"subtitles": [...], ...}。

    注意：未登录时 subtitles 是静默的空列表（2026-08 实测已无
    need_login_subtitle 标记字段），调用方需配合 is_logged_in() 区分
    「没登录」和「没有字幕」。
    """
    # 优先走 wbi 签名端点（当前不强制，但签名更稳妥）
    try:
        img_key, sub_key = _get_wbi_keys(client)
        params = wbi.sign_params({"bvid": bvid, "cid": cid}, img_key, sub_key)
        data = _request_json(client, "/x/player/wbi/v2", params, "获取字幕列表")
        if data.get("code") == 0:
            return data.get("data", {}).get("subtitle", {}) or {}
    except (BiliError, KeyError, ValueError):
        pass
    # 回退：旧端点目前同样可用
    data = _check(_request_json(client, "/x/player/v2", {"bvid": bvid, "cid": cid}, "获取字幕列表"), "获取字幕列表")
    return data.get("subtitle", {}) or {}


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
