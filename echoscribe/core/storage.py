"""Filesystem layout: media cache, user-configured download/save/docs dirs.

All path getters resolve relative paths against the project root
(parent of the ``echoscribe`` package). Absolute paths are honored as-is.
A ``save_dir`` path acts as a base for ``docs_dir`` when the latter is
relative — see ``get_docs_dir``.

Filename safety: ``safe_title`` strips Windows-illegal characters so
the same titles work across platforms.
"""
import os
import re
import tempfile
import time

from .config import load_config


# Project root = parent of the echoscribe package.
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

# Per-process media cache for the web video player. Files are evicted
# by ``cleanup_old_media`` on each ``/api/media/<filename>`` request.
MEDIA_DIR = os.path.join(tempfile.gettempdir(), "funasr_media")
os.makedirs(MEDIA_DIR, exist_ok=True)


def cleanup_old_media(max_age_hours=1):
    """Best-effort: remove media cache files older than ``max_age_hours``."""
    try:
        now = time.time()
        for fname in os.listdir(MEDIA_DIR):
            fpath = os.path.join(MEDIA_DIR, fname)
            if os.path.isfile(fpath) and now - os.path.getmtime(fpath) > max_age_hours * 3600:
                os.remove(fpath)
    except Exception:
        pass


def safe_title(title, max_len=80):
    """Strip Windows-illegal filename characters; truncate to ``max_len``."""
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", title or "untitled")
    return cleaned[:max_len]


def _resolve_under_project_root(path):
    """Return ``path`` if absolute, else join under the project root."""
    if os.path.isabs(path):
        return path
    return os.path.join(_PROJECT_ROOT, path)


def get_download_dir():
    """Return the configured download directory, creating it if needed.

    Returns None when ``download_dir`` is empty (caller should use a
    tempdir in that case).
    """
    cfg = load_config()
    dl_dir = cfg.get("download_dir", "").strip()
    if not dl_dir:
        return None
    dl_dir = _resolve_under_project_root(dl_dir)
    os.makedirs(dl_dir, exist_ok=True)
    return dl_dir


def get_save_dir():
    """Return the configured permanent save directory, or None if unset."""
    cfg = load_config()
    save_dir = cfg.get("save_dir", "").strip()
    if not save_dir:
        return None
    save_dir = _resolve_under_project_root(save_dir)
    os.makedirs(save_dir, exist_ok=True)
    return save_dir


def get_docs_dir():
    """Return the docs directory (always exists). Defaults to project_root/docs.

    If ``docs_dir`` is relative and a ``save_dir`` is configured, the
    docs dir is placed under save_dir; otherwise under the project root.
    """
    cfg = load_config()
    docs_dir = cfg.get("docs_dir", "docs").strip() or "docs"
    if os.path.isabs(docs_dir):
        os.makedirs(docs_dir, exist_ok=True)
        return docs_dir
    base = get_save_dir() or _PROJECT_ROOT
    full = os.path.join(base, docs_dir)
    os.makedirs(full, exist_ok=True)
    return full
