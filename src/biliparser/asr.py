"""本地 ASR 兜底：faster-whisper 转写视频音频（可选依赖组 [asr]）。

为什么需要：2026-08 起 B 站字幕源对新视频大面积「串台」（CDN 返回别的
视频的字幕，语义校验对同题材串台无法可靠判定）。本地转写音频绕开整个
字幕库，内容必然正确。

成本（如实告知用户）：
- 首次使用需下载模型（base ≈ 141MB，small ≈ 466MB，存 ~/.biliparser/models）
- CPU 转写：5 分钟视频约 0.5~1 分钟，20 分钟约 3~5 分钟（M 芯片更快）
- 安装：uv sync --group asr（ctranslate2 体积较大）
"""

import tempfile
from pathlib import Path

import httpx

from .bilibili import UA

MODEL_DIR = Path.home() / ".biliparser" / "models"
DEFAULT_MODEL = "base"  # 中文质量一般但下载小；追求质量可配 small


class AsrError(Exception):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


def transcribe(audio_url: str, title: str = "", model_size: str | None = None,
               progress=print) -> list[dict]:
    """下载音频流并转写，返回字幕行格式 [{from, to, content}, …]（秒）。

    progress 回调用于报告阶段进展（下载/加载模型/转写）。
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise AsrError(
            "未安装语音转写组件 faster-whisper",
            hint="运行 uv sync --group asr 安装（约 200MB，含模型首次另下 141MB）",
        ) from e

    model_size = model_size or DEFAULT_MODEL
    with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as tmp:
        audio_path = Path(tmp.name)
    try:
        progress(f"→ 下载音频 …")
        _download(audio_url, audio_path)
        progress(f"→ 加载模型 {model_size}（首次使用需下载，请稍候）…")
        model = WhisperModel(
            model_size, device="cpu", compute_type="int8",
            download_root=str(MODEL_DIR),
        )
        progress("→ 本地转写中（CPU，比字幕慢但内容必然正确）…")
        segments, _info = model.transcribe(
            str(audio_path), language="zh", beam_size=5, vad_filter=True,
            initial_prompt=title[:60] or None,  # 标题引导专有名词
        )
        lines = [
            {"from": float(s.start), "to": float(s.end), "content": s.text.strip()}
            for s in segments
            if s.text.strip()
        ]
        return lines
    finally:
        audio_path.unlink(missing_ok=True)


def _download(url: str, dest: Path) -> None:
    """音频流下载（hdslb 需要 UA + Referer，不支持 Range 全量拉）。"""
    headers = {
        "User-Agent": UA,
        "Referer": "https://www.bilibili.com/",
    }
    try:
        with httpx.stream("GET", url, headers=headers, timeout=60, follow_redirects=True) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(1 << 16):
                    f.write(chunk)
    except httpx.HTTPError as e:
        raise AsrError(f"音频下载失败：{e.__class__.__name__}") from e
