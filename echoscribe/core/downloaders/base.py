"""Shared helpers used by every downloader: browser/cookie discovery,
HTTP fetch, and file lookup in the download output dir.
"""
import os
import shutil
import urllib.request


# Path where users can drop a manually-exported douyin cookies file.
COOKIES_DIR = os.path.expanduser("~/.funasr_cookies")
DOUYIN_COOKIES_FILE = os.path.join(COOKIES_DIR, "douyin_cookies.txt")


def _browser_installed(browser):
    """Check whether a given browser binary exists on this machine."""
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


def get_douyin_cookies_opts():
    """Build yt-dlp cookie kwargs for douyin. Manual file beats browser cookies."""
    if os.path.exists(DOUYIN_COOKIES_FILE):
        return {"cookiefile": DOUYIN_COOKIES_FILE}
    for browser in ("firefox", "edge", "chrome"):
        if _browser_installed(browser):
            return {"cookiesfrombrowser": (browser,)}
    return None


def find_chrome():
    """Locate a Chromium-family browser usable for CDP. None if unavailable."""
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


def fetch_url(url, headers=None, max_redirects=5):
    """Download URL contents into bytes, manually following redirects."""
    if max_redirects <= 0:
        raise RuntimeError("重定向次数过多")
    req = urllib.request.Request(url)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in (301, 302, 303, 307, 308) and "Location" in resp.headers:
                return fetch_url(resp.headers["Location"], headers, max_redirects - 1)
            return resp.read()
    except Exception as e:
        raise RuntimeError(f"下载失败: {e}")


def find_wav(output_dir, video_id):
    """Locate the WAV produced by yt-dlp; return its path. Raises if none found.

    Tries (in order):
      1. ``<video_id>.wav`` (the expected name)
      2. Any ``*.wav`` in the directory
      3. Any audio container — converts it to WAV in place using ffmpeg
    """
    from ..audio import convert_to_wav  # avoid circular at import time

    expected = os.path.join(output_dir, f"{video_id}.wav")
    if os.path.exists(expected):
        return expected
    for f in os.listdir(output_dir):
        if f.endswith(".wav"):
            return os.path.join(output_dir, f)
    for f in os.listdir(output_dir):
        ext = f.rsplit(".", 1)[-1].lower()
        if ext in ("m4a", "mp3", "flac", "ogg", "opus", "webm", "mp4"):
            src = os.path.join(output_dir, f)
            dst = os.path.join(output_dir, f"{video_id}.wav")
            convert_to_wav(src, dst)
            return dst
    raise RuntimeError("下载完成但未找到音频文件")


def find_video(output_dir):
    """Return the first video file in the dir, or None if there isn't one."""
    for f in os.listdir(output_dir):
        ext = f.rsplit(".", 1)[-1].lower()
        if ext in ("mp4", "webm", "mkv", "avi", "mov"):
            return os.path.join(output_dir, f)
    return None
