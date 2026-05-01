"""音视频转写 + AI 总结 Web 应用。"""

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

import requests
from flask import Flask, Response, jsonify, render_template, request, send_file, stream_with_context

app = Flask(__name__)


def sse_event(data):
    """将 dict 格式化为 SSE data 行。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

# 媒体文件目录（用于视频播放）
MEDIA_DIR = os.path.join(tempfile.gettempdir(), "funasr_media")
os.makedirs(MEDIA_DIR, exist_ok=True)

# ============================================================
# LLM 配置系统
# ============================================================
CONFIG_PATH = os.path.expanduser("~/.funasr_config.json")

DEFAULT_CONFIG = {
    "llm_backend": "custom_api",
    "custom_api": {
        "format": "openai",
        "base_url": "",
        "api_key": "",
        "model": "",
    },
    "download_dir": "downloads",
    "save_dir": "",
    "save_video": True,
    "docs_dir": "docs",
    "feishu": {
        "app_id": "",
        "app_secret": "",
    },
}


def load_config():
    """加载配置，不存在则创建默认配置。"""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
                elif isinstance(v, dict):
                    for kk, vv in v.items():
                        if kk not in cfg[k]:
                            cfg[k][kk] = vv
            return cfg
        except Exception:
            pass
    cfg = DEFAULT_CONFIG.copy()
    save_config(cfg)
    return cfg


def save_config(cfg):
    """保存配置到文件。"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# 全局 FunASR 模型（启动时加载一次）
funasr_model = None

# 浏览器支持的视频格式
BROWSER_VIDEO_FORMATS = {"mp4", "webm", "ogg"}


def cleanup_old_media(max_age_hours=1):
    """清理超过指定时间的媒体文件。"""
    try:
        now = time.time()
        for fname in os.listdir(MEDIA_DIR):
            fpath = os.path.join(MEDIA_DIR, fname)
            if os.path.isfile(fpath) and now - os.path.getmtime(fpath) > max_age_hours * 3600:
                os.remove(fpath)
    except Exception:
        pass


def get_model(status_callback=None):
    """延迟加载 FunASR 模型。"""
    global funasr_model
    if funasr_model is None:
        import torch
        from funasr import AutoModel

        device = "cuda" if torch.cuda.is_available() else "cpu"
        if status_callback:
            status_callback("loading")
        print(f"正在加载 EchoScribe 模型 (设备: {device})...")
        funasr_model = AutoModel(
            model="paraformer-zh",
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            device=device,
            disable_update=True,
        )
        if status_callback:
            status_callback("ready")
        print("EchoScribe 模型加载完成。")
    return funasr_model


def is_model_loaded():
    """检查模型是否已加载。"""
    return funasr_model is not None


def check_deps():
    """检查 ffmpeg 是否可用。"""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("未找到 ffmpeg，请先安装 ffmpeg 并确保其在 PATH 中。")


def normalize_url(url):
    """Normalize URLs that yt-dlp cannot handle directly."""
    import re
    m = re.search(r'modal_id=(\d+)', url)
    if m:
        return f"https://www.douyin.com/video/{m.group(1)}"
    m = re.search(r'douyin\.com/(?:note|share/video)/(\d+)', url)
    if m:
        return f"https://www.douyin.com/video/{m.group(1)}"
    return url


def get_proxy():
    """从环境变量读取代理地址。优先级: YT_PROXY > HTTPS_PROXY > HTTP_PROXY。"""
    return os.environ.get("YT_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")


def is_douyin_url(url):
    """检查是否为抖音链接。"""
    return "douyin.com" in url or "iesdouyin.com" in url


def is_xiaoyuzhou_url(url):
    """检查是否为小宇宙播客链接。"""
    return "xiaoyuzhoufm.com" in url


def download_xiaoyuzhou(url, output_dir, progress_callback=None):
    """从小宇宙网页提取音频 URL 并下载，返回 (wav_path, video_path, metadata)。"""
    import re, json as _json

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resp = requests.get(url, headers=headers, timeout=30)
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

    audio_resp = requests.get(audio_url, headers=headers, timeout=300, stream=True)
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

    metadata = {
        "title": title,
        "duration": duration,
        "id": eid,
        "webpage_url": url,
    }

    if progress_callback:
        progress_callback({"status": "finished"})

    return wav_path, None, metadata


def _browser_installed(browser):
    """检查浏览器是否安装。"""
    if os.name == "nt":
        paths = {
            "chrome": [
                os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            ],
            "edge": [
                os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            ],
            "firefox": [
                os.path.expandvars(r"%ProgramFiles%\Mozilla Firefox\firefox.exe"),
                os.path.expandvars(r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe"),
            ],
        }
        return any(os.path.exists(p) for p in paths.get(browser, []))
    return shutil.which(browser) is not None


COOKIES_DIR = os.path.expanduser("~/.funasr_cookies")
DOUYIN_COOKIES_FILE = os.path.join(COOKIES_DIR, "douyin_cookies.txt")


def get_douyin_cookies_opts():
    """获取抖音 cookies 配置。优先使用手动导出的 cookies 文件。"""
    if os.path.exists(DOUYIN_COOKIES_FILE):
        return {"cookiefile": DOUYIN_COOKIES_FILE}
    for browser in ("firefox", "edge", "chrome"):
        if _browser_installed(browser):
            return {"cookiesfrombrowser": (browser,)}
    return None


def download_audio(url, output_dir, progress_callback=None):
    """使用 yt-dlp 下载音频和视频，返回 (wav_path, video_path, metadata)。"""
    url = normalize_url(url)

    # 抖音链接优先使用 CDP 下载（无需 cookies）
    if is_douyin_url(url):
        try:
            return download_douyin_via_cdp(url, output_dir, progress_callback)
        except Exception as e:
            print(f"[CDP] 抖音 CDP 下载失败，回退到 yt-dlp: {e}")

    # 小宇宙播客链接
    if is_xiaoyuzhou_url(url):
        return download_xiaoyuzhou(url, output_dir, progress_callback)

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

    proxy = get_proxy()
    if proxy:
        ydl_opts["proxy"] = proxy

    if is_douyin_url(url):
        cookie_opts = get_douyin_cookies_opts()
        if cookie_opts:
            ydl_opts.update(cookie_opts)
        else:
            print("警告：未找到浏览器 cookies，抖音下载可能会失败")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        err_msg = str(e)
        if is_douyin_url(url) and ("cookie" in err_msg.lower() or "fresh" in err_msg.lower()):
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

    video_id = metadata["id"]
    wav_path = os.path.join(output_dir, f"{video_id}.wav")
    if not os.path.exists(wav_path):
        for f in os.listdir(output_dir):
            if f.endswith(".wav"):
                wav_path = os.path.join(output_dir, f)
                break
        else:
            for f in os.listdir(output_dir):
                ext = f.rsplit(".", 1)[-1].lower()
                if ext in ("m4a", "mp3", "flac", "ogg", "opus", "webm", "mp4"):
                    src = os.path.join(output_dir, f)
                    wav_path = os.path.join(output_dir, f"{video_id}.wav")
                    convert_to_wav(src, wav_path)
                    break
            else:
                raise RuntimeError("下载完成但未找到音频文件")

    video_path = None
    for f in os.listdir(output_dir):
        ext = f.rsplit(".", 1)[-1].lower()
        if ext in ("mp4", "webm", "mkv", "avi", "mov"):
            video_path = os.path.join(output_dir, f)
            break

    return wav_path, video_path, metadata


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
        err = e.stderr.decode(errors="replace") if e.stderr else str(e)
        raise RuntimeError(f"音频转换失败: {err}")


CHUNK_DURATION_SEC = 30 * 60  # 30 分钟


def probe_duration(path):
    """用 ffprobe 获取音频时长（秒）。"""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def parse_timestamp(ts):
    """将 HH:MM:SS 解析为毫秒。"""
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    elif len(parts) == 2:
        h = 0
        m, s = int(parts[0]), int(parts[1])
    else:
        return 0
    return (h * 3600 + m * 60 + s) * 1000


def split_audio(audio_path, chunk_sec=CHUNK_DURATION_SEC):
    """将长音频按 chunk_sec 秒切分，返回 [(chunk_path, offset_sec), ...]。"""
    duration = probe_duration(audio_path)
    if duration is None or duration <= chunk_sec:
        return [(audio_path, 0)]

    tmp_dir = tempfile.mkdtemp(prefix="funasr_chunk_")
    chunks = []
    idx = 0
    offset = 0.0
    while offset < duration:
        chunk_path = os.path.join(tmp_dir, f"chunk_{idx:03d}.wav")
        cmd = [
            "ffmpeg", "-y",
            "-i", audio_path,
            "-ss", str(offset),
            "-t", str(chunk_sec),
            "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
            chunk_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        chunks.append((chunk_path, offset))
        offset += chunk_sec
        idx += 1
    return chunks


# ============================================================
# Chrome DevTools Protocol 抖音下载器
# ============================================================

def _find_chrome():
    """查找 Chrome/Edge 可执行文件路径（CDP 兼容）。"""
    if os.name == "nt":
        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
    return shutil.which("google-chrome") or shutil.which("chrome") or shutil.which("microsoft-edge")


def download_douyin_via_cdp(url, output_dir, progress_callback=None):
    """通过 Chrome DevTools Protocol 下载抖音视频，返回 (wav_path, video_path, metadata)。"""
    import http.client
    import json as _json
    import re

    import websocket

    chrome_path = _find_chrome()
    if not chrome_path:
        raise RuntimeError("未找到 Chrome 浏览器，无法使用 CDP 下载抖音视频")

    if progress_callback:
        progress_callback({"status": "downloading", "progress": 0})

    # 启动 Chrome headless
    profile_dir = os.path.join(output_dir, f".chrome_cdp_{uuid.uuid4().hex[:8]}")
    # 找一个可用端口
    import socket as _socket
    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as _s:
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
    chrome_proc = subprocess.Popen(chrome_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        # 等待调试端口就绪
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

        # 连接 WebSocket
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
                # 处理事件消息，捕获视频 URL
                if resp.get("method") in ("Network.requestWillBeSent", "Network.responseReceived"):
                    req_url = ""
                    params_data = resp.get("params", {})
                    if "request" in params_data:
                        req_url = params_data["request"].get("url", "")
                    elif "response" in params_data:
                        req_url = params_data["response"].get("url", "")
                    if req_url and ("douyinvod.com" in req_url or
                                    (".mp4" in req_url and "uuu_" not in req_url and "/aweme/" not in req_url)):
                        if req_url not in video_urls:
                            video_urls.append(req_url)

        # 启用网络监听
        cdp_send("Network.enable")
        cdp_send("Page.enable")

        # 导航到抖音链接
        cdp_send("Page.navigate", {"url": url})

        # 等待页面加载并捕获视频 URL
        if progress_callback:
            progress_callback({"status": "downloading", "progress": 20})

        deadline = time.time() + 20  # 最多等 20 秒
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
                    if req_url and ("douyinvod.com" in req_url or
                                    (".mp4" in req_url and "uuu_" not in req_url and "/aweme/" not in req_url)):
                        if req_url not in video_urls:
                            video_urls.append(req_url)
            except websocket.WebSocketTimeoutException:
                pass
            except Exception:
                break
            # 每秒发送进度，保持 SSE 连接活跃
            elapsed = time.time() - wait_start
            pct = min(20 + int(elapsed * 1.5), 45)
            if progress_callback and pct != last_progress:
                progress_callback({"status": "downloading", "progress": pct})
                last_progress = pct

        ws.close()

        if not video_urls:
            raise RuntimeError("CDP 未能捕获到抖音视频 URL")

        # 选择最佳视频 URL
        best_url = video_urls[0]

        if progress_callback:
            progress_callback({"status": "downloading", "progress": 50})

        # 下载视频
        print(f"[CDP] 下载视频: {best_url[:120]}...")
        video_data = _fetch_url(best_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.douyin.com/",
        })

        # 提取 aweme_id 作为文件名
        aweme_id = re.search(r'aweme_id=(\d+)', url) or re.search(r'/video/(\d+)', url)
        video_id = aweme_id.group(1) if aweme_id else "douyin_video"

        # 保存视频
        video_path = os.path.join(output_dir, f"{video_id}.mp4")
        with open(video_path, "wb") as f:
            f.write(video_data)
        print(f"[CDP] 视频已保存: {video_path} ({len(video_data) / 1024 / 1024:.1f} MB)")

        if progress_callback:
            progress_callback({"status": "downloading", "progress": 80})

        # 提取音频
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
        # 清理临时 profile
        try:
            shutil.rmtree(profile_dir, ignore_errors=True)
        except Exception:
            pass


def _fetch_url(url, headers=None, max_redirects=5):
    """下载 URL 内容，支持重定向。"""
    if max_redirects <= 0:
        raise RuntimeError("重定向次数过多")
    import urllib.request
    req = urllib.request.Request(url)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            # 跟随重定向
            if resp.status in (301, 302, 303, 307, 308) and "Location" in resp.headers:
                return _fetch_url(resp.headers["Location"], headers, max_redirects - 1)
            return resp.read()
    except Exception as e:
        raise RuntimeError(f"下载失败: {e}")


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


def group_sentences_by_interval(sentences, interval_ms=120000):
    """将句子按时间间隔分组（默认2分钟）。"""
    if not sentences:
        return []

    groups = []
    current_group = {
        "start_ms": sentences[0].get("_start_ms", 0),
        "sentences": [],
    }

    for sent in sentences:
        start_ms = sent.get("_start_ms", 0)
        if start_ms - current_group["start_ms"] >= interval_ms and current_group["sentences"]:
            group_text = "".join(s["text"] for s in current_group["sentences"])
            groups.append({
                "start": format_timestamp(current_group["start_ms"]),
                "end": current_group["sentences"][-1]["end"],
                "start_ms": current_group["start_ms"],
                "text": group_text,
                "sentence_count": len(current_group["sentences"]),
            })
            current_group = {"start_ms": start_ms, "sentences": []}
        current_group["sentences"].append(sent)

    if current_group["sentences"]:
        group_text = "".join(s["text"] for s in current_group["sentences"])
        groups.append({
            "start": format_timestamp(current_group["start_ms"]),
            "end": current_group["sentences"][-1]["end"],
            "start_ms": current_group["start_ms"],
            "text": group_text,
            "sentence_count": len(current_group["sentences"]),
        })

    return groups


def _transcribe_chunk(audio_path):
    """转写单个音频片段，返回 sentences 和 full_text_parts。"""
    model = get_model()
    results = model.generate(
        input=audio_path,
        batch_size_s=300,
        sentence_timestamp=True,
    )
    sentences = []
    full_text_parts = []
    for result in results:
        sentence_info = result.get("sentence_info", [])
        if sentence_info:
            for sent in sentence_info:
                text = sent.get("text", "")
                start = sent.get("start", 0)
                end = sent.get("end", 0)
                sentences.append({
                    "text": text,
                    "start": start,
                    "end": end,
                })
                full_text_parts.append(text)
        else:
            text = result.get("text", "")
            if text:
                sentences.append({"text": text, "start": 0, "end": 0})
                full_text_parts.append(text)
    return sentences, full_text_parts


def transcribe_audio(audio_path):
    """转写音频，自动切分长音频，返回结构化结果。"""
    chunks = split_audio(audio_path)
    all_sentences = []
    all_full_text = []

    for chunk_path, offset_sec in chunks:
        sentences, text_parts = _transcribe_chunk(chunk_path)
        offset_ms = int(offset_sec * 1000)
        for s in sentences:
            s["start"] += offset_ms
            s["end"] += offset_ms
            s["_start_ms"] = s["start"]
            s["start"] = format_timestamp(s["start"])
            s["end"] = format_timestamp(s["end"])
        all_sentences.extend(sentences)
        all_full_text.extend(text_parts)

        # 清理临时分段文件
        if chunk_path != audio_path and os.path.exists(chunk_path):
            try:
                os.remove(chunk_path)
            except OSError:
                pass

    # 清理临时分段目录
    if len(chunks) > 1:
        chunk_dir = os.path.dirname(chunks[0][0])
        try:
            os.rmdir(chunk_dir)
        except OSError:
            pass

    grouped = group_sentences_by_interval(all_sentences)

    for s in all_sentences:
        s.pop("_start_ms", None)

    return {
        "sentences": all_sentences,
        "grouped": grouped,
        "full_text": "".join(all_full_text),
    }


# ============================================================
# LLM 调用抽象层
# ============================================================

MAX_LLM_CHARS = 80000


def truncate_text(text):
    if len(text) > MAX_LLM_CHARS:
        return text[:MAX_LLM_CHARS] + "\n\n[文本过长，已截断]"
    return text


def call_llm(prompt):
    """统一 LLM 调用入口，根据配置分发到不同后端。"""
    cfg = load_config()
    backend = cfg.get("llm_backend", "custom_api")

    try:
        if backend == "claude_cli":
            return _call_claude_cli(prompt)
        elif backend == "custom_api":
            return _call_custom_api(prompt, cfg.get("custom_api", {}))
        else:
            raise RuntimeError(f"未知的 LLM 后端: {backend}")
    except RuntimeError as e:
        raise RuntimeError(
            f"AI 后端 ({backend}) 调用失败: {e}\n"
            f"请在页面右上角「设置」中切换到其他可用的 AI 后端。"
        )


def _call_claude_cli(prompt):
    """通过 claude --print 调用。"""
    if os.name == "nt":
        for path_dir in os.environ.get("PATH", "").split(os.pathsep):
            cmd = os.path.join(path_dir, "claude.cmd")
            if os.path.exists(cmd):
                break
        else:
            cmd = shutil.which("claude")
    else:
        cmd = shutil.which("claude")
    if not cmd:
        raise RuntimeError("未找到 claude 命令，请确保 Claude CLI 已安装并在 PATH 中。")
    try:
        result = subprocess.run(
            [cmd, "--print"],
            input=prompt, capture_output=True, text=True, timeout=300, encoding="utf-8",
        )
        if result.returncode != 0:
            raise RuntimeError(f"Claude CLI 错误: {result.stderr}")
        return result.stdout.strip()
    except FileNotFoundError:
        raise RuntimeError("未找到 claude 命令，请确保 Claude CLI 已安装并在 PATH 中。")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Claude CLI 响应超时（5分钟）")


def _call_custom_api(prompt, api_cfg):
    """通过自定义 API 调用 LLM（OpenAI 兼容格式）。"""
    base_url = api_cfg.get("base_url", "").rstrip("/")
    api_key = api_cfg.get("api_key", "")
    model = api_cfg.get("model", "")

    if not base_url:
        raise RuntimeError("自定义 API 未配置 Base URL，请在设置中填写。")

    url = f"{base_url}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=300)
        if resp.status_code >= 400:
            try:
                err = resp.json()
                msg = err.get("error", {}).get("message", "") or str(err)
            except Exception:
                msg = resp.text[:500]
            raise RuntimeError(f"API 返回 {resp.status_code}: {msg}")
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except requests.exceptions.Timeout:
        raise RuntimeError("自定义 API 响应超时（5分钟）")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"自定义 API 请求失败: {e}")
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"自定义 API 响应格式异常: {e}")


def summarize_with_llm(text):
    """使用配置的 LLM 后端总结转写文本。"""
    text = truncate_text(text)
    prompt = (
        "请对以下音视频转写内容进行总结。要求：\n"
        "1. 提炼核心主题和关键信息\n"
        "2. 按时间线或逻辑结构分段总结\n"
        "3. 保留重要的数据、人名、专有名词\n"
        "4. 使用中文回答\n"
        "5. 使用多级标题（# ## ### ####）组织内容，便于生成思维导图\n\n"
        f"转写内容：\n{text}"
    )
    return call_llm(prompt)


def deepen_with_llm(text, question):
    """基于转写文本追问细节。"""
    text = truncate_text(text)
    prompt = (
        "以下是一段音视频转写内容。请根据用户的问题进行针对性回答。\n\n"
        f"转写内容：\n{text}\n\n"
        f"用户问题：{question}\n\n"
        "请使用中文回答，并用多级标题组织内容。"
    )
    return call_llm(prompt)


# ============================================================
# Flask Routes
# ============================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/media/<filename>")
def serve_media(filename):
    """Serve media files for video playback."""
    cleanup_old_media()
    file_path = os.path.join(MEDIA_DIR, filename)
    if not os.path.exists(file_path):
        return jsonify({"error": "File not found"}), 404
    return send_file(file_path)


@app.route("/api/transcribe-url", methods=["POST"])
def api_transcribe_url():
    """通过 URL 转写音视频（SSE 流式进度）。"""
    data = request.get_json()
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"success": False, "error": "请输入 URL"}), 400

    # 下载目录：优先使用配置的 download_dir，否则用系统临时目录
    dl_dir = get_download_dir()
    if dl_dir:
        output_dir = dl_dir
        is_temp_dir = False
    else:
        output_dir = tempfile.mkdtemp(prefix="funasr_web_")
        is_temp_dir = True

    model_already_loaded = is_model_loaded()
    total_steps = 4 if model_already_loaded else 5
    # 步骤映射：model_loading=1, downloading=2, transcribing=3(sum+1), summarizing=4(sum+1)
    # 如果模型已加载，步骤从 downloading 开始为 1

    def generate():
        q = queue.Queue()
        step_offset = 0 if model_already_loaded else 1
        print(f"[SSE] generate() started, model_loaded={model_already_loaded}, total={total_steps}")

        def _worker():
            try:
                check_deps()

                # Step 1 (可选): 模型加载
                if not model_already_loaded:
                    q.put(sse_event({
                        "stage": "model_loading",
                        "text": "正在加载 AI 模型（首次需下载，请耐心等待）...",
                        "step": 1, "total": total_steps,
                    }))
                    def _model_status(status):
                        if status == "ready":
                            q.put(sse_event({
                                "stage": "model_loaded",
                                "text": "AI 模型加载完成",
                                "step": 1, "total": total_steps,
                            }))
                    get_model(status_callback=_model_status)

                # Step 2: 下载音频
                dl_step = 1 + step_offset
                q.put(sse_event({
                    "stage": "downloading",
                    "text": "正在下载音频...",
                    "step": dl_step, "total": total_steps,
                }))
                def _dl_progress(info):
                    if info.get("status") == "downloading":
                        q.put(sse_event({
                            "stage": "downloading",
                            "text": "正在下载音频...",
                            "step": dl_step, "total": total_steps,
                            "progress": info.get("progress"),
                        }))
                wav_path, video_path, metadata = download_audio(url, output_dir, progress_callback=_dl_progress)

                cfg = load_config()
                save_video = cfg.get("save_video", True)

                media_filename = None
                if video_path and os.path.exists(video_path):
                    if save_video:
                        ext = video_path.rsplit(".", 1)[-1].lower()
                        media_filename = f"{uuid.uuid4().hex[:12]}.{ext}"
                        shutil.copy2(video_path, os.path.join(MEDIA_DIR, media_filename))
                    elif not is_temp_dir:
                        # 不保存视频时，删除下载目录中的原始视频
                        try:
                            os.remove(video_path)
                        except OSError:
                            pass

                # Step 3: 转写
                transcribe_step = 2 + step_offset
                q.put(sse_event({
                    "stage": "transcribing",
                    "text": "正在转写音频...",
                    "step": transcribe_step, "total": total_steps,
                }))
                transcription = transcribe_audio(wav_path)

                # Step 4: AI 总结
                summarize_step = 3 + step_offset
                q.put(sse_event({
                    "stage": "summarizing",
                    "text": "正在生成 AI 总结...",
                    "step": summarize_step, "total": total_steps,
                }))
                summary = ""
                try:
                    summary = summarize_with_llm(transcription["full_text"])
                except Exception as e:
                    summary = f"AI 总结失败: {e}"

                # 如果配置了 save_dir，永久保存音视频
                save_dir = get_save_dir()
                if save_dir:
                    try:
                        if wav_path and os.path.exists(wav_path):
                            shutil.copy2(wav_path, os.path.join(save_dir, os.path.basename(wav_path)))
                        if save_video and video_path and os.path.exists(video_path):
                            shutil.copy2(video_path, os.path.join(save_dir, os.path.basename(video_path)))
                    except Exception as e:
                        print(f"[SSE] 保存到 save_dir 失败: {e}")

                # 自动保存 .md/.txt 到 docs_dir
                try:
                    docs_dir = get_docs_dir()
                    safe_title = (metadata["title"] or "untitled").replace("/", "_").replace("\\", "_")[:50]
                    if summary:
                        with open(os.path.join(docs_dir, f"{safe_title}_summary.md"), "w", encoding="utf-8") as f:
                            f.write(f"# {metadata['title']} - AI 总结\n\n{summary}\n")
                    full_text = transcription.get("full_text", "")
                    if full_text:
                        with open(os.path.join(docs_dir, f"{safe_title}_transcription.txt"), "w", encoding="utf-8") as f:
                            f.write(full_text)
                except Exception as e:
                        print(f"[SSE] 保存到 save_dir 失败: {e}")

                q.put(sse_event({
                    "stage": "complete",
                    "data": {
                        "success": True,
                        "metadata": {
                            "title": metadata["title"],
                            "duration": format_duration(metadata["duration"]),
                            "source": url,
                        },
                        "transcription": transcription,
                        "summary": summary,
                        "video_url": f"/api/media/{media_filename}" if media_filename else None,
                    },
                }))

            except Exception as e:
                print(f"[SSE] worker error: {e}")
                q.put(sse_event({"stage": "error", "error": str(e)}))
            finally:
                q.put(None)

        threading.Thread(target=_worker, daemon=True).start()

        while True:
            event = q.get()
            if event is None:
                print("[SSE] generator done")
                break
            # 解析事件类型用于日志
            try:
                evt_data = json.loads(event.split("data: ", 1)[1].strip())
                print(f"[SSE] yielding: stage={evt_data.get('stage')}, step={evt_data.get('step')}")
            except Exception:
                print(f"[SSE] yielding event")
            yield event
            import sys
            sys.stdout.flush()

        if is_temp_dir:
            shutil.rmtree(output_dir, ignore_errors=True)

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/transcribe-file", methods=["POST"])
def api_transcribe_file():
    """通过上传文件转写音视频（SSE 流式进度）。"""
    file = request.files.get("file")

    if not file or file.filename == "":
        return jsonify({"success": False, "error": "请上传文件"}), 400

    tmpdir = tempfile.mkdtemp(prefix="funasr_web_")
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "wav"
    upload_path = os.path.join(tmpdir, f"upload.{ext}")
    file.save(upload_path)

    model_already_loaded = is_model_loaded()
    total_steps = 3 if model_already_loaded else 4

    def generate():
        try:
            check_deps()

            step_offset = 0 if model_already_loaded else 1

            # Step 1 (可选): 模型加载
            if not model_already_loaded:
                yield sse_event({
                    "stage": "model_loading",
                    "text": "正在加载 AI 模型（首次需下载，请耐心等待）...",
                    "step": 1, "total": total_steps,
                })
                get_model()
                yield sse_event({
                    "stage": "model_loaded",
                    "text": "AI 模型加载完成",
                    "step": 1, "total": total_steps,
                })

            # Step 2: 格式转换
            convert_step = 1 + step_offset
            yield sse_event({
                "stage": "converting",
                "text": "正在转换音频格式...",
                "step": convert_step, "total": total_steps,
            })
            wav_path = os.path.join(tmpdir, "audio.wav")
            convert_to_wav(upload_path, wav_path)

            # Step 3: 转写
            transcribe_step = 2 + step_offset
            yield sse_event({
                "stage": "transcribing",
                "text": "正在转写音频...",
                "step": transcribe_step, "total": total_steps,
            })
            transcription = transcribe_audio(wav_path)

            # Step 4: AI 总结
            summarize_step = 3 + step_offset
            yield sse_event({
                "stage": "summarizing",
                "text": "正在生成 AI 总结...",
                "step": summarize_step, "total": total_steps,
            })
            summary = ""
            try:
                summary = summarize_with_llm(transcription["full_text"])
            except Exception as e:
                summary = f"AI 总结失败: {e}"

            media_filename = None
            if ext in BROWSER_VIDEO_FORMATS:
                media_filename = f"{uuid.uuid4().hex[:12]}.{ext}"
                shutil.copy2(upload_path, os.path.join(MEDIA_DIR, media_filename))
            elif ext in ("mkv", "avi", "mov", "flv", "wmv", "ts"):
                media_filename = f"{uuid.uuid4().hex[:12]}.mp4"
                media_path = os.path.join(MEDIA_DIR, media_filename)
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", upload_path, "-c", "copy", media_path],
                        check=True, capture_output=True,
                    )
                except Exception:
                    media_filename = None

            # 自动保存 .md/.txt 到 docs_dir
            try:
                docs_dir = get_docs_dir()
                safe_title = (file.filename or "untitled").rsplit(".", 1)[0].replace("/", "_").replace("\\", "_")[:50]
                if summary:
                    with open(os.path.join(docs_dir, f"{safe_title}_summary.md"), "w", encoding="utf-8") as f:
                        f.write(f"# {file.filename} - AI 总结\n\n{summary}\n")
                full_text = transcription.get("full_text", "")
                if full_text:
                    with open(os.path.join(docs_dir, f"{safe_title}_transcription.txt"), "w", encoding="utf-8") as f:
                        f.write(full_text)
            except Exception as e:
                print(f"[SSE] 保存到 docs_dir 失败: {e}")

            yield sse_event({
                "stage": "complete",
                "data": {
                    "success": True,
                    "metadata": {
                        "title": file.filename,
                        "duration": "未知",
                        "source": file.filename,
                    },
                    "transcription": transcription,
                    "summary": summary,
                    "video_url": f"/api/media/{media_filename}" if media_filename else None,
                },
            })

        except Exception as e:
            yield sse_event({"stage": "error", "error": str(e)})
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/deepen", methods=["POST"])
def api_deepen():
    """基于转写结果追问细节。"""
    data = request.get_json()
    question = data.get("question", "").strip()
    full_text = data.get("full_text", "").strip()

    if not question:
        return jsonify({"success": False, "error": "请输入问题"}), 400
    if not full_text:
        return jsonify({"success": False, "error": "缺少转写文本"}), 400

    try:
        answer = deepen_with_llm(full_text, question)
        return jsonify({"success": True, "answer": answer})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/config", methods=["GET"])
def api_get_config():
    """读取 LLM 配置（api_key 脱敏）。"""
    cfg = load_config()
    safe_cfg = json.loads(json.dumps(cfg))
    key = safe_cfg.get("custom_api", {}).get("api_key", "")
    if key:
        safe_cfg["custom_api"]["api_key_masked"] = key[:4] + "****" + key[-4:] if len(key) > 8 else "****"
        safe_cfg["custom_api"]["has_key"] = True
    else:
        safe_cfg["custom_api"]["api_key_masked"] = ""
        safe_cfg["custom_api"]["has_key"] = False
    safe_cfg["custom_api"].pop("api_key", None)
    return jsonify({"success": True, "config": safe_cfg})


@app.route("/api/config", methods=["POST"])
def api_save_config():
    """保存 LLM 配置。"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "无效数据"}), 400

    cfg = load_config()

    backend = data.get("llm_backend")
    if backend in ("claude_cli", "codex_cli", "custom_api"):
        cfg["llm_backend"] = backend

    api_data = data.get("custom_api", {})
    if api_data:
        if "format" in api_data:
            cfg["custom_api"]["format"] = api_data["format"]
        if "base_url" in api_data:
            cfg["custom_api"]["base_url"] = api_data["base_url"]
        if "model" in api_data:
            cfg["custom_api"]["model"] = api_data["model"]
        if api_data.get("api_key"):
            cfg["custom_api"]["api_key"] = api_data["api_key"]

    if "download_dir" in data:
        cfg["download_dir"] = data["download_dir"]
    if "save_dir" in data:
        cfg["save_dir"] = data["save_dir"]
    if "save_video" in data:
        cfg["save_video"] = bool(data["save_video"])
    if "docs_dir" in data:
        cfg["docs_dir"] = data["docs_dir"]

    save_config(cfg)
    return jsonify({"success": True})


def get_download_dir():
    """获取下载目录绝对路径，不存在则创建。"""
    cfg = load_config()
    dl_dir = cfg.get("download_dir", "").strip()
    if not dl_dir:
        return None
    # 相对路径基于项目目录
    if not os.path.isabs(dl_dir):
        dl_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), dl_dir)
    os.makedirs(dl_dir, exist_ok=True)
    return dl_dir


def get_save_dir():
    """获取永久保存目录绝对路径，不存在则创建。"""
    cfg = load_config()
    save_dir = cfg.get("save_dir", "").strip()
    if not save_dir:
        return None
    if not os.path.isabs(save_dir):
        save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), save_dir)
    os.makedirs(save_dir, exist_ok=True)
    return save_dir


def get_docs_dir():
    """获取文档保存目录（docs_dir），相对于 save_dir 或项目目录。"""
    cfg = load_config()
    docs_dir = cfg.get("docs_dir", "docs").strip() or "docs"
    if os.path.isabs(docs_dir):
        os.makedirs(docs_dir, exist_ok=True)
        return docs_dir
    # 优先放在 save_dir 下，否则项目目录下
    base = get_save_dir() or os.path.dirname(os.path.abspath(__file__))
    full = os.path.join(base, docs_dir)
    os.makedirs(full, exist_ok=True)
    return full


@app.route("/api/save-file", methods=["POST"])
def api_save_file():
    """保存导出文件到 docs_dir。"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "无效数据"}), 400

    docs_dir = get_docs_dir()
    if not docs_dir:
        return jsonify({"success": False, "error": "未配置文档目录"}), 400

    filename = data.get("filename", "").strip()
    content = data.get("content", "")
    if not filename:
        return jsonify({"success": False, "error": "文件名为空"}), 400

    # 安全检查：防止路径穿越
    filename = os.path.basename(filename)
    filepath = os.path.join(docs_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return jsonify({"success": True, "path": filepath})


if __name__ == "__main__":
    print("=" * 50)
    print("EchoScribe - AI 音视频转写")
    print("=" * 50)
    print("访问 http://localhost:5000 开始使用")
    print(f"LLM 配置: {CONFIG_PATH}")
    print("=" * 50)
    app.run(debug=False, host="0.0.0.0", port=5000)
