"""Generic yt-dlp downloader path. Used for every URL except douyin (CDP)
and xiaoyuzhou (custom HTML scraper).
"""
import os

from ..audio import get_proxy
from .base import (
    DOUYIN_COOKIES_FILE,
    COOKIES_DIR,
    find_video,
    find_wav,
    get_douyin_cookies_opts,
)


def _is_douyin(url):
    return "douyin.com" in url or "iesdouyin.com" in url


def download_via_ytdlp(
    url,
    output_dir,
    *,
    progress_callback=None,
    cookies=None,
    part=None,
    proxy=None,
):
    """Download with yt-dlp. Returns ``(wav_path, video_path, metadata)``.

    Args:
        progress_callback: receives ``{"status": "downloading", "progress": pct}``
            during download and ``{"status": "finished"}`` when done.
        cookies: explicit cookie file path (overrides browser auto-detection).
        part: B站 playlist item to download (1-indexed).
        proxy: explicit proxy URL (overrides ``YT_PROXY`` env).
    """
    import yt_dlp

    def _dl_hook(d):
        if progress_callback and d.get("status") == "downloading":
            pct_str = d.get("_percent_str", "").strip().rstrip("%")
            try:
                pct = float(pct_str)
            except (ValueError, TypeError):
                pct = None
            progress_callback({"status": "downloading", "progress": pct})
        elif progress_callback and d.get("status") == "finished":
            progress_callback({"status": "finished"})

    outtmpl = os.path.join(output_dir, "%(id)s.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extract_flat": False,
        "progress_hooks": [_dl_hook],
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
    elif _is_douyin(url):
        cookie_opts = get_douyin_cookies_opts()
        if cookie_opts:
            ydl_opts.update(cookie_opts)
        else:
            print("警告：未找到浏览器 cookies，抖音下载可能会失败")

    if part is not None:
        ydl_opts["playlist_items"] = str(part)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        err_msg = str(e)
        if _is_douyin(url) and ("cookie" in err_msg.lower() or "fresh" in err_msg.lower()):
            os.makedirs(COOKIES_DIR, exist_ok=True)
            raise RuntimeError(
                "抖音下载失败：无法读取浏览器 cookies。\n\n"
                "【推荐方案】手动导出 cookies：\n"
                "1. 在 Edge/Chrome 中安装扩展「Get cookies.txt LOCALLY」\n"
                "2. 在浏览器中打开 https://www.douyin.com 并登录\n"
                "3. 点击扩展图标 -> Export -> 保存为：\n"
                f"   {DOUYIN_COOKIES_FILE}\n"
                "4. 重新点击「开始转写」\n\n"
                f"原始错误: {err_msg}"
            )
        raise RuntimeError(f"下载失败: {e}")
    except yt_dlp.utils.ExtractorError as e:
        raise RuntimeError(f"无法解析 URL: {e}")

    metadata = {
        "title": info.get("title", "未知标题"),
        "duration": info.get("duration", 0),
        "id": info.get("id", ""),
        "webpage_url": info.get("webpage_url", url),
    }

    wav_path = find_wav(output_dir, metadata["id"])
    video_path = find_video(output_dir)
    return wav_path, video_path, metadata
