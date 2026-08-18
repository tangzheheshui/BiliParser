"""B 站 wbi 参数签名。

算法来自 bilibili-API-collect 文档镜像（docs/misc/sign/wbi.md）。
当前 player 接口并不强制 wbi 校验，但保留实现以防官方重新启用。
"""

import hashlib
import time
import urllib.parse

# 官方前端内置的 64 位混淆表
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def get_mixin_key(img_key: str, sub_key: str) -> str:
    """img_key + sub_key 按混淆表重排后取前 32 位。"""
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def sign_params(
    params: dict, img_key: str, sub_key: str, wts: int | None = None
) -> dict:
    """返回带 wts / w_rid 的签名参数副本（不修改入参）。

    wts 可注入固定值，便于测试。
    """
    mixin_key = get_mixin_key(img_key, sub_key)
    signed = {
        k: "".join(ch for ch in str(v) if ch not in "!'()*")
        for k, v in params.items()
    }
    signed["wts"] = int(time.time()) if wts is None else wts
    signed = dict(sorted(signed.items()))
    # 文档要求：十六进制大写、空格编码为 %20（quote_via=quote 满足两者）
    query = urllib.parse.urlencode(signed, quote_via=urllib.parse.quote)
    signed["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return signed


def extract_keys(wbi_img: dict) -> tuple[str, str]:
    """从 nav 接口返回的 wbi_img 中提取 img_key / sub_key。"""
    img_url = wbi_img["img_url"]  # 形如 https://i0.hdslb.com/bfs/wbi/<key>.png
    sub_url = wbi_img["sub_url"]
    return (
        img_url.rsplit("/", 1)[1].split(".")[0],
        sub_url.rsplit("/", 1)[1].split(".")[0],
    )
