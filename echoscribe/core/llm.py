"""LLM backend dispatch (Claude CLI or any OpenAI-compatible API) + summarization.

Prompt templates live in ``<project_root>/skills/`` as Markdown files.
The prompt body is the section under ``## Prompt 内容`` — this lets users
edit prompts without touching code (the rest of the .md is documentation).

Two backends:
- ``claude_cli``: shells out to ``claude --print``
- ``custom_api``: POST to ``{base_url}/v1/chat/completions``
"""
import os
import shutil
import subprocess

import requests

from .config import load_config


# Prompt templates directory — sibling of the echoscribe package.
_PROMPTS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "skills")
)

# Hard cap on transcript length sent to the LLM. Anything longer is
# truncated with a marker so the model knows it's incomplete.
MAX_LLM_CHARS = 80000


def truncate_text(text):
    if len(text) > MAX_LLM_CHARS:
        return text[:MAX_LLM_CHARS] + "\n\n[文本过长，已截断]"
    return text


def call_llm(prompt):
    """Dispatch to the configured backend; map low-level errors to a hint to switch."""
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
    """Run ``claude --print`` with the prompt on stdin."""
    if os.name == "nt":
        cmd = None
        for path_dir in os.environ.get("PATH", "").split(os.pathsep):
            candidate = os.path.join(path_dir, "claude.cmd")
            if os.path.exists(candidate):
                cmd = candidate
                break
        if cmd is None:
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
    """POST to an OpenAI-compatible chat completion endpoint."""
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


def load_prompt_template(prompt_type="default"):
    """Load and extract the prompt body from ``skills/<file>.md``.

    ``prompt_type``: ``"default"`` → ``summarize_prompt.md``;
                     ``"long"`` → ``summarize_long_prompt.md``.
    Returns the text under the first ``## Prompt 内容`` heading; if no
    such heading exists, returns the whole file.
    """
    filename = "summarize_long_prompt.md" if prompt_type == "long" else "summarize_prompt.md"
    prompt_path = os.path.join(_PROMPTS_DIR, filename)
    if not os.path.exists(prompt_path):
        raise RuntimeError(f"未找到 prompt 模板文件: {prompt_path}")

    with open(prompt_path, "r", encoding="utf-8") as f:
        content = f.read()

    if "## Prompt 内容" in content:
        start = content.index("## Prompt 内容") + len("## Prompt 内容")
        end = len(content)
        next_section = content.find("\n## ", start)
        if next_section != -1:
            end = next_section
        return content[start:end].strip()
    return content.strip()


def summarize_with_llm(text, prompt_type="default"):
    """Render the summarize template with ``{text}`` substituted; call the LLM."""
    text = truncate_text(text)
    template = load_prompt_template(prompt_type)
    prompt = template.replace("{text}", text)
    return call_llm(prompt)


def deepen_with_llm(text, question):
    """Ask a follow-up question against the transcript."""
    text = truncate_text(text)
    prompt = (
        "以下是一段音视频转写内容。请根据用户的问题进行针对性回答。\n\n"
        f"转写内容：\n{text}\n\n"
        f"用户问题：{question}\n\n"
        "请使用中文回答，并用多级标题组织内容。"
    )
    return call_llm(prompt)
