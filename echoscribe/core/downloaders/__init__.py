"""Downloader dispatcher: route a URL to the right backend.

Adds two CLI-only kwargs (``cookies``, ``part``, ``proxy``) to the
shared signature. Existing web callers can ignore them.
"""
import re

from .douyin_cdp import download_douyin_via_cdp
from .xiaoyuzhou import download_xiaoyuzhou
from .ytdlp import download_via_ytdlp


def is_douyin_url(url):
    return "douyin.com" in url or "iesdouyin.com" in url


def is_xiaoyuzhou_url(url):
    return "xiaoyuzhoufm.com" in url


def normalize_url(url):
    """Rewrite the awkward douyin URL shapes into a yt-dlp-friendly form."""
    m = re.search(r'modal_id=(\d+)', url)
    if m:
        return f"https://www.douyin.com/video/{m.group(1)}"
    m = re.search(r'douyin\.com/(?:note|share/video)/(\d+)', url)
    if m:
        return f"https://www.douyin.com/video/{m.group(1)}"
    return url


def download_audio(
    url,
    output_dir,
    progress_callback=None,
    *,
    cookies=None,
    part=None,
    proxy=None,
):
    """Download audio (and video where available). Returns ``(wav, video_or_None, metadata)``.

    Routing:
        douyin.com → CDP first, fall back to yt-dlp on failure
        xiaoyuzhoufm.com → custom HTML scraper
        everything else → yt-dlp
    """
    url = normalize_url(url)

    if is_douyin_url(url):
        try:
            return download_douyin_via_cdp(url, output_dir, progress_callback)
        except Exception as e:
            print(f"[CDP] 抖音 CDP 下载失败，回退到 yt-dlp: {e}")

    if is_xiaoyuzhou_url(url):
        return download_xiaoyuzhou(url, output_dir, progress_callback)

    return download_via_ytdlp(
        url,
        output_dir,
        progress_callback=progress_callback,
        cookies=cookies,
        part=part,
        proxy=proxy,
    )
