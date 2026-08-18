"""wbi 签名离线测试。

已知测试向量来自 bilibili-API-collect 文档（docs/misc/sign/wbi.md）。
"""

from biliparser.wbi import extract_keys, get_mixin_key, sign_params

IMG_KEY = "7cd084941338484aae1ad9425b84077c"
SUB_KEY = "4932caff0ff746eab6f01bf08b70ac45"


def test_mixin_key_known_vector():
    # 文档给出的官方示例：这对 key 的 mixin key 是固定值
    assert get_mixin_key(IMG_KEY, SUB_KEY) == "ea1db124af3c7062474693fa704f4ff8"


def test_sign_params_known_vector():
    # 文档 Rust demo 中的完整测试向量：
    # foo=114&bar=514&zab=1919810, wts=1702204169
    # → w_rid=8f6f2b5b3d485fe1886cec6a0be8c5d4
    signed = sign_params(
        {"foo": "114", "bar": "514", "zab": 1919810}, IMG_KEY, SUB_KEY, wts=1702204169
    )
    assert signed["w_rid"] == "8f6f2b5b3d485fe1886cec6a0be8c5d4"


def test_sign_params_shape_and_determinism():
    params = {"bvid": "BV1GJ411x7h7", "cid": 80105091}
    signed = sign_params(params, IMG_KEY, SUB_KEY, wts=1700000000)
    assert signed["wts"] == 1700000000
    assert len(signed["w_rid"]) == 32  # md5 hex
    # 同输入同输出（确定性）
    again = sign_params(params, IMG_KEY, SUB_KEY, wts=1700000000)
    assert signed == again
    # 原参数不被修改
    assert "w_rid" not in params and "wts" not in params


def test_sign_params_filters_special_chars():
    # 文档要求 value 中的 !'()* 字符要剔除
    signed = sign_params({"q": "a!'()*b"}, IMG_KEY, SUB_KEY, wts=1)
    plain = sign_params({"q": "ab"}, IMG_KEY, SUB_KEY, wts=1)
    assert signed["w_rid"] == plain["w_rid"]


def test_extract_keys_from_nav_payload():
    wbi_img = {
        "img_url": f"https://i0.hdslb.com/bfs/wbi/{IMG_KEY}.png",
        "sub_url": f"https://i0.hdslb.com/bfs/wbi/{SUB_KEY}.png",
    }
    assert extract_keys(wbi_img) == (IMG_KEY, SUB_KEY)
