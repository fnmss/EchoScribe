"""User configuration: load/save/defaults.

Stored as JSON at ``CONFIG_PATH`` (``~/.funasr_config.json``). Missing
top-level and one-level-deep keys are filled from ``DEFAULT_CONFIG``
on every load. Extra keys not present in defaults are PRESERVED — see
``tests/test_config.py::test_extra_keys_preserved``.

Future direction (refactor step 8): wrap the dict in a ``TypedDict``
for editor support without losing round-trip safety. Do NOT switch to
a frozen dataclass: that would silently drop user-added keys.
"""
import json
import os


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
    """Read config from CONFIG_PATH, filling missing defaults. Create on first run."""
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
    """Persist config dict back to CONFIG_PATH (UTF-8, indented)."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
