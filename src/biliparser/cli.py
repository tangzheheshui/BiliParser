"""命令行入口与主流程编排。"""

import argparse
import sys
import traceback

from . import bilibili, config, subtitle, summarizer


def _fmt_duration(seconds) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="biliparse",
        description="B 站视频字幕提取与 AI 总结（输入视频链接或 BV 号）",
    )
    p.add_argument("url", help="视频链接或 BV 号")
    p.add_argument("--page", type=int, default=1, metavar="N", help="分 P 序号，从 1 开始（默认 1）")
    p.add_argument("--lang", default=None, help="指定字幕语言（如 zh-CN、ai_zh），默认自动选择")
    p.add_argument("--subtitle-only", action="store_true", help="只输出字幕全文，不调用 LLM")
    p.add_argument(
        "--save", nargs="?", const="AUTO", metavar="FILE",
        help="把结果保存为 Markdown 文件（缺省文件名为 <BV号>.md）",
    )
    p.add_argument("--debug", action="store_true", help="出错时打印完整堆栈")
    return p


def run(args: argparse.Namespace) -> int:
    need = ("sessdata",) if args.subtitle_only else ("sessdata", "glm_api_key")
    cfg = config.load_config(require=need)

    bvid = bilibili.parse_bvid(args.url)
    print(f"→ 获取视频信息 …", flush=True)
    client = bilibili.make_client(cfg.sessdata)
    info = bilibili.get_video_info(client, bvid)

    title = info.get("title", "")
    owner = (info.get("owner") or {}).get("name", "未知")
    duration = info.get("duration", 0)
    pages = info.get("pages") or []
    if pages:
        if not 1 <= args.page <= len(pages):
            raise bilibili.BiliError(
                f"视频只有 {len(pages)} 个分 P，--page {args.page} 超出范围"
            )
        page = pages[args.page - 1]
        cid = page["cid"]
        part = page.get("part") or title
    else:
        cid = info["cid"]
        part = title
    page_tag = f"（P{args.page} {part}）" if len(pages) > 1 else ""
    print(f"  {title} {page_tag}", flush=True)
    print(f"  UP 主：{owner}｜时长：{_fmt_duration(duration)}", flush=True)

    print("→ 拉取字幕列表 …", flush=True)
    sub_info = bilibili.get_subtitle_info(client, bvid, cid)
    subs = sub_info.get("subtitles") or []
    if not subs:
        # 未登录时 B 站静默返回空列表；用 nav 登录态区分两种原因
        if not bilibili.is_logged_in(client):
            raise bilibili.BiliError(
                "拿不到字幕：SESSDATA 未配置或已失效",
                hint="浏览器登录 B 站后按 F12 → 应用 → Cookie 复制 SESSDATA，"
                f"填入 {config.CONFIG_PATH} 或设置环境变量 BILI_SESSDATA",
            )
        raise bilibili.BiliError(
            "该视频没有可用字幕",
            hint="纯音乐、方言较重或较新的视频常无 AI 字幕；本工具暂不支持语音转写（ASR）",
        )

    sub = subtitle.pick_subtitle(subs, args.lang)
    if sub is None:
        raise bilibili.BiliError(
            f"找不到语言为 {args.lang} 的字幕",
            hint=f"该视频可选：{'、'.join(subtitle.available_langs(subs))}",
        )
    print(f"  字幕源：{sub.get('lan_doc') or sub.get('lan')}", flush=True)

    print("→ 下载字幕 …", flush=True)
    lines = bilibili.download_subtitle(client, sub["subtitle_url"])
    if not lines:
        raise bilibili.BiliError("字幕文件内容为空")
    transcript = subtitle.build_transcript(lines)
    print(f"  共 {len(lines)} 行字幕，约 {len(transcript)} 字符", flush=True)

    if args.subtitle_only:
        output = f"# {title} {page_tag}\n\n```\n{transcript}\n```\n"
        print(transcript)
    else:
        print(f"→ AI（{cfg.glm_model}）总结中 …", flush=True)
        summary = summarizer.summarize(transcript, title, cfg)
        output = (
            f"# {title} {page_tag}\n\n"
            f"- UP 主：{owner}\n"
            f"- 链接：https://www.bilibili.com/video/{bvid}\n"
            f"- 时长：{_fmt_duration(duration)}｜字幕：{sub.get('lan_doc') or sub.get('lan')}\n\n"
            f"---\n\n{summary}\n"
        )
        print(output)

    if args.save:
        path = f"{bvid}.md" if args.save == "AUTO" else args.save
        with open(path, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"\n已保存：{path}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台/重定向编码保护
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    args = build_argparser().parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        return 130
    except (config.ConfigError, bilibili.BiliError, summarizer.SummarizeError) as e:
        hint = getattr(e, "hint", None)
        print(f"\n错误：{e}", file=sys.stderr)
        if hint:
            print(f"提示：{hint}", file=sys.stderr)
        return 1
    except Exception:
        if args.debug:
            traceback.print_exc()
        else:
            last = traceback.format_exc().strip().splitlines()[-1]
            print(f"\n发生未预期错误：{last}（加 --debug 查看详情）", file=sys.stderr)
        return 1
