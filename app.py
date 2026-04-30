"""音视频转写 + AI 总结 Web 应用。"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid

import requests
from flask import Flask, jsonify, render_template, request, send_file

app = Flask(__name__)

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
    return DEFAULT_CONFIG.copy()


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


def get_model():
    """延迟加载 FunASR 模型。"""
    global funasr_model
    if funasr_model is None:
        import torch
        from funasr import AutoModel

        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"正在加载 EchoScribe 模型 (设备: {device})...")
        funasr_model = AutoModel(
            model="paraformer-zh",
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            device=device,
            disable_update=True,
        )
        print("EchoScribe 模型加载完成。")
    return funasr_model


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


def download_audio(url, output_dir):
    """使用 yt-dlp 下载音频和视频，返回 (wav_path, video_path, metadata)。"""
    import yt_dlp

    url = normalize_url(url)

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


def transcribe_audio(audio_path):
    """转写音频，返回结构化结果。"""
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
                    "start": format_timestamp(start),
                    "end": format_timestamp(end),
                    "_start_ms": start,
                })
                full_text_parts.append(text)
        else:
            text = result.get("text", "")
            if text:
                sentences.append({"text": text, "start": "", "end": "", "_start_ms": 0})
                full_text_parts.append(text)

    grouped = group_sentences_by_interval(sentences)

    for s in sentences:
        s.pop("_start_ms", None)

    return {
        "sentences": sentences,
        "grouped": grouped,
        "full_text": "".join(full_text_parts),
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
    """通过 URL 转写音视频。"""
    data = request.get_json()
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"success": False, "error": "请输入 URL"}), 400

    tmpdir = tempfile.mkdtemp(prefix="funasr_web_")
    try:
        check_deps()
        wav_path, video_path, metadata = download_audio(url, tmpdir)

        media_filename = None
        if video_path and os.path.exists(video_path):
            ext = video_path.rsplit(".", 1)[-1].lower()
            media_filename = f"{uuid.uuid4().hex[:12]}.{ext}"
            shutil.copy2(video_path, os.path.join(MEDIA_DIR, media_filename))

        transcription = transcribe_audio(wav_path)

        summary = ""
        try:
            summary = summarize_with_llm(transcription["full_text"])
        except Exception as e:
            summary = f"AI 总结失败: {e}"

        return jsonify({
            "success": True,
            "metadata": {
                "title": metadata["title"],
                "duration": format_duration(metadata["duration"]),
                "source": url,
            },
            "transcription": transcription,
            "summary": summary,
            "video_url": f"/api/media/{media_filename}" if media_filename else None,
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@app.route("/api/transcribe-file", methods=["POST"])
def api_transcribe_file():
    """通过上传文件转写音视频。"""
    file = request.files.get("file")

    if not file or file.filename == "":
        return jsonify({"success": False, "error": "请上传文件"}), 400

    tmpdir = tempfile.mkdtemp(prefix="funasr_web_")
    try:
        check_deps()

        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "wav"
        upload_path = os.path.join(tmpdir, f"upload.{ext}")
        file.save(upload_path)

        wav_path = os.path.join(tmpdir, "audio.wav")
        convert_to_wav(upload_path, wav_path)

        transcription = transcribe_audio(wav_path)

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

        return jsonify({
            "success": True,
            "metadata": {
                "title": file.filename,
                "duration": "未知",
                "source": file.filename,
            },
            "transcription": transcription,
            "summary": summary,
            "video_url": f"/api/media/{media_filename}" if media_filename else None,
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


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

    save_config(cfg)
    return jsonify({"success": True})


if __name__ == "__main__":
    print("=" * 50)
    print("EchoScribe - AI 音视频转写")
    print("=" * 50)
    print("访问 http://localhost:5000 开始使用")
    print(f"LLM 配置: {CONFIG_PATH}")
    print("=" * 50)
    app.run(debug=False, host="0.0.0.0", port=5000)
