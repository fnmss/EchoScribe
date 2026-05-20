# Repository Guidelines

## Project Overview

EchoScribe is a Flask web app and CLI toolkit for downloading audio/video from URLs, transcribing with FunASR, and generating AI summaries. The current codebase contains a large legacy `app.py` plus a newer `echoscribe/core` package that is gradually extracting reusable audio, downloader, model, config, storage, LLM, and transcription logic.

## Layout

- `app.py` is the main Flask server and currently owns most routes and legacy pipeline code.
- `templates/index.html`, `static/app.js`, and `static/style.css` implement the web UI.
- `transcribe_url.py` is the CLI transcription entry point.
- `feishu_bot.py` integrates with Feishu/Lark.
- `echoscribe/core/` contains the modularized runtime code. Prefer extending this package for new reusable logic instead of adding more helpers to `app.py`.
- `skills/` stores local prompt templates.
- `docs/`, `downloads/`, `.cache/`, `__pycache__/`, and `.pytest_cache/` are generated/runtime data and should not be used for source changes unless the task explicitly asks for artifacts there.

## Environment

- Python 3.9-3.11 is expected.
- Install Python dependencies with `pip install -r requirements.txt` after creating a virtual environment.
- `ffmpeg` must be available on `PATH` for audio conversion and probing.
- FunASR model loading can be slow and may download large model files on first run.
- Runtime user config is stored outside the repo at `~/.funasr_config.json`; do not commit local secrets or API keys.

## Common Commands

- Run the web app: `python app.py`
- Run the CLI: `python transcribe_url.py "<URL>"`
- Run tests when test sources are present: `pytest`

On this Windows workspace, PowerShell profile loading may emit an execution-policy warning. Use a no-profile shell invocation for cleaner command output when possible.

## Coding Guidelines

- Keep changes narrow and preserve the existing Chinese-language UI/content where present.
- Use UTF-8 for file IO and JSON persistence.
- Preserve user config keys on load/save. `echoscribe/core/config.py` intentionally keeps unknown keys rather than replacing the config with a strict schema.
- Prefer `echoscribe/core/*` modules for new shared code; leave route orchestration and request/response formatting in `app.py`.
- Do not commit generated media, transcription outputs, cache files, downloaded files, model artifacts, or local configuration.
- Be careful with long-running/model-loading paths in tests. Unit-test pure helpers and mock network, filesystem-heavy, browser, ffmpeg, FunASR, and LLM calls when possible.

## Current Workspace Notes

- The repository may have local user edits. Check `git status` before changing files and do not revert unrelated work.
- If Git reports dubious ownership in this sandbox, use `git -c safe.directory=D:/github/FunASR ...` for read-only Git inspection.
- At initialization time, `requirements.txt` contained merge-conflict markers. Resolve that before relying on dependency installation.
