"""Douyin downloader via Chrome DevTools Protocol.

Spawns headless Chrome, opens the douyin URL, listens for the actual
``douyinvod.com`` video request through the DevTools Network domain,
then fetches that URL directly. This sidesteps the cookie/signature
verification that yt-dlp can't handle.
"""
import http.client
import json as _json
import os
import re
import shutil
import socket
import subprocess
import time
import uuid

import websocket

from ..audio import convert_to_wav
from .base import fetch_url, find_chrome


def download_douyin_via_cdp(url, output_dir, progress_callback=None):
    """Download a douyin video via headless Chrome + CDP.

    Returns ``(wav_path, video_path, metadata)``. Raises RuntimeError
    if Chrome is unavailable or the video URL can't be captured.
    """
    chrome_path = find_chrome()
    if not chrome_path:
        raise RuntimeError("未找到 Chrome 浏览器，无法使用 CDP 下载抖音视频")

    if progress_callback:
        progress_callback({"status": "downloading", "progress": 0})

    # Pick a free port for the debug endpoint and a unique profile dir.
    profile_dir = os.path.join(output_dir, f".chrome_cdp_{uuid.uuid4().hex[:8]}")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
        _s.bind(("127.0.0.1", 0))
        port = _s.getsockname()[1]
    chrome_args = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--headless=new",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-extensions",
        "--mute-audio",
    ]
    chrome_proc = subprocess.Popen(
        chrome_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    try:
        # Wait for the debug port to come up and find the page socket.
        ws_url = None
        for i in range(30):
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/json")
                resp = conn.getresponse()
                pages = _json.loads(resp.read())
                conn.close()
                for p in pages:
                    if p.get("type") == "page":
                        ws_url = p.get("webSocketDebuggerUrl")
                        break
                if ws_url:
                    break
            except Exception:
                pass
            if progress_callback:
                progress_callback({"status": "downloading", "progress": min(5 + i, 15)})
            time.sleep(1)

        if not ws_url:
            raise RuntimeError("无法连接到 Chrome 调试端口")

        ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
        msg_id = [0]
        video_urls = []

        def cdp_send(method, params=None):
            msg_id[0] += 1
            payload = {"id": msg_id[0], "method": method}
            if params:
                payload["params"] = params
            ws.send(_json.dumps(payload))
            while True:
                resp = _json.loads(ws.recv())
                if resp.get("id") == msg_id[0]:
                    return resp
                # Drain network events to capture video URLs as a side effect.
                if resp.get("method") in ("Network.requestWillBeSent", "Network.responseReceived"):
                    req_url = ""
                    params_data = resp.get("params", {})
                    if "request" in params_data:
                        req_url = params_data["request"].get("url", "")
                    elif "response" in params_data:
                        req_url = params_data["response"].get("url", "")
                    if req_url and (
                        "douyinvod.com" in req_url
                        or (".mp4" in req_url and "uuu_" not in req_url and "/aweme/" not in req_url)
                    ):
                        if req_url not in video_urls:
                            video_urls.append(req_url)

        cdp_send("Network.enable")
        cdp_send("Page.enable")
        cdp_send("Page.navigate", {"url": url})

        if progress_callback:
            progress_callback({"status": "downloading", "progress": 20})

        deadline = time.time() + 20  # capture window
        wait_start = time.time()
        last_progress = 20
        while time.time() < deadline and not video_urls:
            try:
                ws.settimeout(1)
                resp = _json.loads(ws.recv())
                if resp.get("method") in ("Network.requestWillBeSent", "Network.responseReceived"):
                    req_url = ""
                    params_data = resp.get("params", {})
                    if "request" in params_data:
                        req_url = params_data["request"].get("url", "")
                    elif "response" in params_data:
                        req_url = params_data["response"].get("url", "")
                    if req_url and (
                        "douyinvod.com" in req_url
                        or (".mp4" in req_url and "uuu_" not in req_url and "/aweme/" not in req_url)
                    ):
                        if req_url not in video_urls:
                            video_urls.append(req_url)
            except websocket.WebSocketTimeoutException:
                pass
            except Exception:
                break
            # Surface progress every second so the SSE connection stays warm.
            elapsed = time.time() - wait_start
            pct = min(20 + int(elapsed * 1.5), 45)
            if progress_callback and pct != last_progress:
                progress_callback({"status": "downloading", "progress": pct})
                last_progress = pct

        ws.close()

        if not video_urls:
            raise RuntimeError("CDP 未能捕获到抖音视频 URL")

        best_url = video_urls[0]

        if progress_callback:
            progress_callback({"status": "downloading", "progress": 50})

        print(f"[CDP] 下载视频: {best_url[:120]}...")
        video_data = fetch_url(best_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.douyin.com/",
        })

        aweme_id = re.search(r'aweme_id=(\d+)', url) or re.search(r'/video/(\d+)', url)
        video_id = aweme_id.group(1) if aweme_id else "douyin_video"

        video_path = os.path.join(output_dir, f"{video_id}.mp4")
        with open(video_path, "wb") as f:
            f.write(video_data)
        print(f"[CDP] 视频已保存: {video_path} ({len(video_data) / 1024 / 1024:.1f} MB)")

        if progress_callback:
            progress_callback({"status": "downloading", "progress": 80})

        wav_path = os.path.join(output_dir, f"{video_id}.wav")
        convert_to_wav(video_path, wav_path)

        if progress_callback:
            progress_callback({"status": "finished"})

        metadata = {
            "title": f"抖音视频 {video_id}",
            "duration": 0,
            "id": video_id,
            "webpage_url": url,
        }
        return wav_path, video_path, metadata

    finally:
        chrome_proc.terminate()
        try:
            chrome_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            chrome_proc.kill()
        try:
            shutil.rmtree(profile_dir, ignore_errors=True)
        except Exception:
            pass
