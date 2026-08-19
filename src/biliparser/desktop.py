"""桌面端入口：起本地服务（复用 web 模块）+ pywebview 原生窗口。

安装：uv sync --group desktop（pywebview + pyinstaller）
运行：uv run biliparser-desktop [--server https://lic.example.com]
打包：packaging/build-macos.sh

未装 pywebview 时回退到系统浏览器（开发调试用）。
"""

import argparse
import socket
import threading
import webbrowser

from . import config, web


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="biliparser-desktop", description="BiliParser 桌面版")
    parser.add_argument("--server", metavar="URL", help="授权服务器地址（写入配置并启用发行模式）")
    parser.add_argument("--port", type=int, default=None, help="本地服务端口（默认自动挑选）")
    args = parser.parse_args(argv)

    if args.server:
        config.update_config({"managed.server_url": args.server.rstrip("/")})
    cfg = config.load_config()

    port = args.port or _free_port()
    server = web.make_server(cfg, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    print(f"BiliParser 桌面版启动：{url}", flush=True)
    if cfg.managed_server:
        print(f"发行模式：授权服务器 {cfg.managed_server}", flush=True)

    try:
        import webview  # pywebview

        webview.create_window(
            "BiliParser", url, width=1440, height=900,
            min_size=(1080, 680), background_color="#101418",
        )
        webview.start()
    except ImportError:
        print("（未安装 pywebview，用浏览器打开；安装：uv sync --group desktop）")
        webbrowser.open(url)
        try:
            threading.Event().wait()  # 主线程挂起，Ctrl+C 退出
        except KeyboardInterrupt:
            pass
    finally:
        server.shutdown()
        server.server_close()
    print("已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
