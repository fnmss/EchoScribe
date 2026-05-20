"""Xiaoyuzhou (小宇宙) podcast downloader.

The site is a Next.js app that ships full episode metadata in a
``__NEXT_DATA__`` <script> tag. We grab the audio URL from there
and download with the requests library; no yt-dlp extractor exists.
"""
import json as _json
import os
import re

import requests

from ..audio import convert_to_wav


_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def download_xiaoyuzhou(url, output_dir, progress_callback=None):
    """Download xiaoyuzhou episode audio. Returns ``(wav_path, None, metadata)``."""
    resp = requests.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()

    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.DOTALL)
    if not m:
        raise RuntimeError("无法解析小宇宙页面：未找到 __NEXT_DATA__")

    data = _json.loads(m.group(1))
    episode = data.get("props", {}).get("pageProps", {}).get("episode")
    if not episode:
        raise RuntimeError("无法解析小宇宙页面：未找到 episode 数据")

    audio_url = None
    enclosure = episode.get("enclosure", {})
    if isinstance(enclosure, dict):
        audio_url = enclosure.get("url")
    if not audio_url:
        media = episode.get("media", {})
        if isinstance(media, dict):
            source = media.get("source", {})
            if isinstance(source, dict):
                audio_url = source.get("url")
    if not audio_url:
        media_key = episode.get("mediaKey", "")
        if media_key:
            audio_url = f"https://media.xyzcdn.net/{media_key}"

    if not audio_url:
        raise RuntimeError("无法获取小宇宙音频地址")

    title = episode.get("title", "未知标题")
    duration = episode.get("duration", 0)
    eid = episode.get("eid", "unknown")

    if progress_callback:
        progress_callback({"status": "downloading", "progress": None})

    audio_resp = requests.get(audio_url, headers=_HEADERS, timeout=300, stream=True)
    audio_resp.raise_for_status()

    ext = "m4a"
    ct = audio_resp.headers.get("Content-Type", "")
    if "mp3" in ct or audio_url.endswith(".mp3"):
        ext = "mp3"

    raw_path = os.path.join(output_dir, f"{eid}.{ext}")
    total = int(audio_resp.headers.get("Content-Length", 0))
    downloaded = 0
    with open(raw_path, "wb") as f:
        for chunk in audio_resp.iter_content(chunk_size=8192):
            f.write(chunk)
            if total > 0:
                downloaded += len(chunk)
                pct = downloaded * 100 / total
                if progress_callback:
                    progress_callback({"status": "downloading", "progress": round(pct, 1)})

    wav_path = os.path.join(output_dir, f"{eid}.wav")
    convert_to_wav(raw_path, wav_path)

    if progress_callback:
        progress_callback({"status": "finished"})

    metadata = {
        "title": title,
        "duration": duration,
        "id": eid,
        "webpage_url": url,
    }
    return wav_path, None, metadata
