"""命令行入口与主流程编排。"""

import argparse
import sys
import traceback

from . import bilibili, config, meta, subtitle, summarizer


def _fmt_duration(seconds) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _output_doc(title, page_tag, owner, bvid, duration, source, body) -> str:
    return (
        f"# {title} {page_tag}\n\n"
        f"- UP 主：{owner}\n"
        f"- 链接：https://www.bilibili.com/video/{bvid}\n"
        f"- 时长：{duration}｜字幕：{source}\n\n"
        f"---\n\n{body}\n"
    )


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="biliparse",
        description="B 站视频字幕提取与 AI 总结（输入视频链接或 BV 号）",
    )
    p.add_argument("url", help="视频链接或 BV 号")
    p.add_argument("--page", type=int, default=1, metavar="N", help="分 P 序号，从 1 开始（默认 1）")
    p.add_argument("--lang", default=None, help="指定字幕语言（如 zh-CN、ai_zh），默认自动选择")
    p.add_argument("--subtitle-only", action="store_true", help="只输出字幕全文，不调用 LLM")
    p.add_argument("--detailed", action="store_true", help="详尽版总结（不漏话题、保留具体数字与金句）")
    p.add_argument(
        "--save", nargs="?", const="AUTO", metavar="FILE",
        help="把结果保存为 Markdown 文件（缺省文件名为 <BV号>.md）",
    )
    p.add_argument("--debug", action="store_true", help="出错时打印完整堆栈")
    return p


def run(args: argparse.Namespace) -> int:
    # sessdata 仅字幕链路必需；总结模式拿不到字幕时可降级为元数据+热评
    need = ("sessdata",) if args.subtitle_only else ("glm_api_key",)
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
        logged_in = bilibili.is_logged_in(client)
        reason = "SESSDATA 未配置或已失效" if not logged_in else "该视频没有可用字幕"
        if args.subtitle_only:
            if not logged_in:
                raise bilibili.BiliError(
                    f"拿不到字幕：{reason}",
                    hint="浏览器登录 B 站后按 F12 → 应用 → Cookie 复制 SESSDATA，"
                    f"填入 {config.CONFIG_PATH} 或设置环境变量 BILI_SESSDATA",
                )
            raise bilibili.BiliError(
                f"{reason}，无法输出字幕全文",
                hint="纯音乐、方言较重或较新的视频常无 AI 字幕；本工具暂不支持语音转写（ASR）",
            )

        # 降级：用公开元数据 + 热评让 LLM 做推断性总结
        print(f"  ⚠ {reason}，降级为「元数据 + 热评」总结（推断性结果，质量有限）", flush=True)
        if not logged_in:
            print(
                f"  提示：在 {config.CONFIG_PATH} 配置 sessdata 后可走完整字幕总结",
                flush=True,
            )
        print("→ 拉取标签与热门评论 …", flush=True)
        tags = bilibili.get_tags(client, bvid)
        comments = bilibili.get_hot_comments(client, info["aid"])
        meta_context = meta.build_meta_context(info, tags, comments)
        print(f"  标签 {len(tags)} 个｜评论 {len(comments)} 条", flush=True)
        print(f"→ AI（{cfg.glm_model}）总结中 …", flush=True)
        summary = summarizer.summarize_meta(meta_context, title, cfg)
        output = _output_doc(
            title, page_tag, owner, bvid, _fmt_duration(duration),
            f"无（{reason}，降级模式）", summary,
        )
        print(output)
    else:
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
            mode = "详尽" if args.detailed else "标准"
            print(f"→ AI（{cfg.glm_model}）{mode}总结中 …", flush=True)
            summary = summarizer.summarize(transcript, title, cfg, detailed=args.detailed)
            output = _output_doc(
                title, page_tag, owner, bvid, _fmt_duration(duration),
                sub.get("lan_doc") or sub.get("lan"), summary,
            )
            print(output)

    if args.save:
        path = args.save
        if path == "AUTO":
            path = f"{bvid}-详细.md" if args.detailed else f"{bvid}.md"
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
