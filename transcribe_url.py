#!/usr/bin/env python3
"""从 URL 下载音频并使用 FunASR 转写为文字。"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time


def check_deps():
    """检查 ffmpeg 是否可用。"""
    if not shutil.which("ffmpeg"):
        print("错误: 未找到 ffmpeg，请先安装 ffmpeg 并确保其在 PATH 中。")
        print("  下载地址: https://ffmpeg.org/download.html")
        sys.exit(1)


def get_proxy():
    """从环境变量读取代理地址。优先级: YT_PROXY > HTTPS_PROXY > HTTP_PROXY。"""
    return os.environ.get("YT_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")


def download_audio(url, output_dir, cookies=None, part=None, proxy=None):
    """使用 yt-dlp 下载音频，返回 (文件路径, 元数据)。"""
    import yt_dlp

    outtmpl = os.path.join(output_dir, "%(id)s.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extract_flat": False,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
                "preferredquality": "0",
            }
        ],
    }

    effective_proxy = proxy or get_proxy()
    if effective_proxy:
        ydl_opts["proxy"] = effective_proxy

    if cookies:
        ydl_opts["cookiefile"] = cookies

    if part is not None:
        ydl_opts["playlist_items"] = str(part)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        err_msg = str(e).lower()
        print(f"错误: 下载失败 - {url}")
        print(f"  原因: {e}")
        if "login" in err_msg or "cookie" in err_msg or "fresh" in err_msg:
            cookie_path = os.path.expanduser("~/.funasr_cookies/douyin_cookies.txt")
            print()
            print("  浏览器 cookies 读取失败。推荐手动导出 cookies：")
            print("  1. 安装浏览器扩展「Get cookies.txt LOCALLY」")
            print("  2. 在浏览器中打开抖音网页版并登录")
            print("  3. 点击扩展图标 → Export → 保存为：")
            print(f"     {cookie_path}")
            print("  4. 重新运行此命令")
            print()
            print(f"  或使用 --cookies 参数指定其他 cookies 文件路径")
        sys.exit(1)
    except yt_dlp.utils.ExtractorError as e:
        print(f"错误: 无法解析 URL - {url}")
        print(f"  原因: {e}")
        sys.exit(1)

    metadata = {
        "title": info.get("title", "未知标题"),
        "duration": info.get("duration", 0),
        "id": info.get("id", ""),
        "webpage_url": info.get("webpage_url", url),
    }

    # 查找下载的 WAV 文件
    video_id = metadata["id"]
    wav_path = os.path.join(output_dir, f"{video_id}.wav")
    if not os.path.exists(wav_path):
        # yt-dlp 后处理器可能保留原扩展名，查找目录中的 wav 文件
        for f in os.listdir(output_dir):
            if f.endswith(".wav"):
                wav_path = os.path.join(output_dir, f)
                break
        else:
            # 没有 wav 文件，查找任意音频文件进行转换
            for f in os.listdir(output_dir):
                ext = f.rsplit(".", 1)[-1].lower()
                if ext in ("m4a", "mp3", "flac", "ogg", "opus", "webm"):
                    src = os.path.join(output_dir, f)
                    wav_path = os.path.join(output_dir, f"{video_id}.wav")
                    convert_to_wav(src, wav_path)
                    break
            else:
                print("错误: 下载完成但未找到音频文件")
                sys.exit(1)

    return wav_path, metadata


def convert_to_wav(input_path, output_path):
    """使用 ffmpeg 将音频转换为 16kHz 单声道 WAV。"""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"错误: 音频转换失败")
        print(f"  原因: {e.stderr.decode(errors='replace') if e.stderr else str(e)}")
        sys.exit(1)


def transcribe(audio_path, model_name="paraformer-zh", device="auto", batch_size_s=300):
    """使用 FunASR 转写音频，返回结果列表。"""
    import torch
    from funasr import AutoModel

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"

    print(f"正在加载模型 (设备: {device})...")
    try:
        model = AutoModel(
            model=model_name,
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            device=device,
        )
    except RuntimeError as e:
        if "out of memory" in str(e).lower() and device == "cuda":
            print("警告: GPU 显存不足，回退到 CPU 模式...")
            device = "cpu"
            model = AutoModel(
                model=model_name,
                vad_model="fsmn-vad",
                punc_model="ct-punc",
                device=device,
            )
        else:
            print(f"错误: 模型加载失败 - {e}")
            sys.exit(1)

    print("正在转写...")
    start_time = time.time()
    try:
        results = model.generate(
            input=audio_path,
            batch_size_s=batch_size_s,
            sentence_timestamp=True,
        )
    except RuntimeError as e:
        if "out of memory" in str(e).lower() and device == "cuda":
            print("警告: GPU 显存不足，回退到 CPU 模式并重试...")
            device = "cpu"
            model = AutoModel(
                model=model_name,
                vad_model="fsmn-vad",
                punc_model="ct-punc",
                device=device,
            )
            results = model.generate(
                input=audio_path,
                batch_size_s=batch_size_s,
                sentence_timestamp=True,
            )
        else:
            print(f"错误: 转写失败 - {e}")
            sys.exit(1)

    elapsed = time.time() - start_time
    print(f"转写完成，耗时 {elapsed:.1f} 秒")
    return results


def format_timestamp(ms):
    """将毫秒转换为 HH:MM:SS 字符串。"""
    seconds = int(ms / 1000)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_duration(seconds):
    """将秒数转换为 MM:SS 或 HH:MM:SS 字符串。"""
    if seconds is None:
        return "未知"
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_output(results, metadata, original_url):
    """格式化转写结果为可读文本。"""
    lines = []
    sep = "=" * 40

    lines.append(sep)
    lines.append(f"标题: {metadata['title']}")
    lines.append(f"URL: {metadata['webpage_url']}")
    lines.append(f"时长: {format_duration(metadata['duration'])}")
    lines.append(sep)
    lines.append("")

    full_text_parts = []

    for result in results:
        sentence_info = result.get("sentence_info", [])
        if sentence_info:
            for sent in sentence_info:
                text = sent.get("text", "")
                start = sent.get("start", 0)
                end = sent.get("end", 0)
                ts_start = format_timestamp(start)
                ts_end = format_timestamp(end)
                lines.append(f"[{ts_start} - {ts_end}] {text}")
                full_text_parts.append(text)
        else:
            # 没有句子级别时间戳，输出完整文本
            text = result.get("text", "")
            if text:
                lines.append(text)
                full_text_parts.append(text)

    if full_text_parts:
        lines.append("")
        lines.append(sep)
        lines.append("完整文本:")
        lines.append("".join(full_text_parts))
        lines.append(sep)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="从 URL 下载音频并使用 FunASR 转写为文字"
    )
    parser.add_argument("url", help="视频/音频 URL（Bilibili、YouTube 等）")
    parser.add_argument("-o", "--output", help="输出文件路径（默认输出到终端）")
    parser.add_argument("--model", default="paraformer-zh", help="ASR 模型名称（默认: paraformer-zh）")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"],
                        help="计算设备（默认: auto）")
    parser.add_argument("--batch-size", type=int, default=300,
                        help="VAD 批处理大小，单位秒（默认: 300）")
    parser.add_argument("--cookies", help="cookies 文件路径（用于需要登录的视频）")
    parser.add_argument("--proxy", help="代理地址 (如 socks5://host:port)，默认读取 YT_PROXY 环境变量")
    parser.add_argument("--part", type=int, help="Bilibili 分P编号（默认: 第一P）")
    parser.add_argument("--save-audio", help="保存下载的音频到指定路径")

    args = parser.parse_args()

    # 检查依赖
    check_deps()

    # 创建临时目录
    tmpdir = tempfile.mkdtemp(prefix="funasr_transcribe_")
    try:
        # Step 1: 下载音频
        print(f"正在下载音频: {args.url}")
        wav_path, metadata = download_audio(args.url, tmpdir, args.cookies, args.part, args.proxy)
        print(f"下载完成: {metadata['title']}")

        # 保存音频副本（如果用户指定）
        if args.save_audio:
            shutil.copy2(wav_path, args.save_audio)
            print(f"音频已保存到: {args.save_audio}")

        # Step 2: 转写
        results = transcribe(wav_path, args.model, args.device, args.batch_size)

        # Step 3: 格式化输出
        output_text = format_output(results, metadata, args.url)

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output_text)
            print(f"转写结果已保存到: {args.output}")
        else:
            print()
            print(output_text)

    finally:
        # 清理临时文件
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
