#!/usr/bin/env python3
"""飞书机器人 — 通过 WebSocket 长连接接收链接，调用 EchoScribe 管线转写并回传结果。"""

import json
import os
import re
import threading
import time

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateFileRequest,
    CreateFileRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
import qrcode

from app import (
    download_audio,
    transcribe_audio,
    summarize_with_llm,
    load_config,
    get_download_dir,
    check_deps,
)

URL_PATTERN = re.compile(
    r"https?://[^\s<>\"']+",
    re.IGNORECASE,
)


def extract_urls(text):
    """从文本中提取所有 URL。"""
    return URL_PATTERN.findall(text or "")


def build_client(app_id, app_secret):
    """构建飞书 API 客户端。"""
    return (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .log_level(lark.LogLevel.INFO)
        .build()
    )


# ── 消息发送 ──────────────────────────────────────────────


def reply_text(client, message_id, text):
    """回复文本消息。"""
    req = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .msg_type("text")
            .content(json.dumps({"text": text}))
            .build()
        )
        .build()
    )
    resp = client.im.v1.message.reply(req)
    if not resp.success():
        print(f"[飞书] 回复文本失败: code={resp.code} msg={resp.msg}")


def reply_post(client, message_id, title, md_lines):
    """回复富文本消息（post 格式），将 md 文本按行拆分为段落。"""
    content_blocks = []
    for line in md_lines.split("\n"):
        if line.strip():
            content_blocks.append([{"tag": "text", "text": line + "\n"}])
    if not content_blocks:
        content_blocks.append([{"tag": "text", "text": "(空)"}])

    post_body = {
        "zh_cn": {
            "title": title,
            "content": content_blocks,
        }
    }
    req = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .msg_type("post")
            .content(json.dumps(post_body, ensure_ascii=False))
            .build()
        )
        .build()
    )
    resp = client.im.v1.message.reply(req)
    if not resp.success():
        print(f"[飞书] 回复富文本失败: code={resp.code} msg={resp.msg}")


def upload_file(client, file_path, file_type="stream"):
    """上传文件到飞书，返回 file_key。"""
    file_name = os.path.basename(file_path)
    with open(file_path, "rb") as f:
        req = (
            CreateFileRequest.builder()
            .request_body(
                CreateFileRequestBody.builder()
                .file_type(file_type)
                .file_name(file_name)
                .file(f)
                .build()
            )
            .build()
        )
        resp = client.im.v1.file.create(req)
    if not resp.success():
        print(f"[飞书] 上传文件失败: code={resp.code} msg={resp.msg}")
        return None
    return resp.data.file_key


def reply_file(client, message_id, file_key):
    """回复文件消息。"""
    req = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .msg_type("file")
            .content(json.dumps({"file_key": file_key}))
            .build()
        )
        .build()
    )
    resp = client.im.v1.message.reply(req)
    if not resp.success():
        print(f"[飞书] 回复文件失败: code={resp.code} msg={resp.msg}")


# ── 管线执行 ──────────────────────────────────────────────


def run_pipeline(client, message_id, url, config, need_text=True, need_summary=True, is_long=False):
    """在子线程中执行管线：下载 → 转写 → 总结 → 回传。
    need_text: 是否发送转写原文
    need_summary: 是否发送 AI 总结
    is_long: 是否使用长视频访谈 prompt
    """
    check_deps()
    download_dir = get_download_dir()
    if not download_dir:
        download_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
    docs_dir = config.get("docs_dir", "docs")
    if not os.path.isabs(docs_dir):
        docs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), docs_dir)
    os.makedirs(download_dir, exist_ok=True)
    os.makedirs(docs_dir, exist_ok=True)

    try:
        # Step 1: 下载
        print(f"[管线] 开始下载: {url}")
        wav_path, video_path, metadata = download_audio(url, download_dir)
        title = metadata.get("title", "未知标题")
        print(f"[管线] 下载完成: {title}")

        # Step 2: 转写
        print("[管线] 开始转写...")
        result = transcribe_audio(wav_path)
        full_text = result["full_text"]
        print(f"[管线] 转写完成，共 {len(full_text)} 字")

        # 保存转写文件
        safe_title = re.sub(r'[\\/:*?"<>|]', "_", title)[:80]
        txt_path = os.path.join(docs_dir, f"{safe_title}_transcription.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(full_text)

        # Step 3: 总结（按需执行）
        summary = None
        md_path = None
        if need_summary:
            prompt_type = "long" if is_long else "default"
            print(f"[管线] 开始 AI 总结（{'长视频' if is_long else '默认'}模式）...")
            summary = summarize_with_llm(full_text, prompt_type)
            print("[管线] 总结完成")

            # 保存总结文件
            md_path = os.path.join(docs_dir, f"{safe_title}_summary.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(summary)

        # Step 4: 回传结果
        if need_summary and summary:
            reply_post(client, message_id, f"📄 {title}", summary)
            if md_path:
                file_key = upload_file(client, md_path)
                if file_key:
                    reply_file(client, message_id, file_key)

        if need_text:
            file_key = upload_file(client, txt_path)
            if file_key:
                reply_file(client, message_id, file_key)

        print(f"[管线] 全部完成: {title}")

    except Exception as e:
        error_msg = f"处理失败: {e}"
        print(f"[管线] {error_msg}")
        reply_text(client, message_id, error_msg)


# ── 事件处理 ──────────────────────────────────────────────


def parse_command(text):
    """解析消息中的指令关键词。
    返回 (need_text, need_summary, is_long):
    - 包含"文本"：发送转写原文
    - 包含"总结"：发送 AI 总结
    - 包含"长"：使用长视频访谈 prompt
    - 都不包含：默认只发总结
    - 都包含：都发
    """
    has_text = "文本" in text
    has_summary = "总结" in text
    is_long = "长" in text
    if has_text or has_summary:
        return has_text, has_summary, is_long
    return False, True, is_long


def make_event_handler(client, config):
    """创建消息事件处理器。"""

    def on_message(data):
        try:
            event = data.event
            msg = event.message
            message_id = msg.message_id
            chat_id = msg.chat_id

            # 只处理文本消息
            if msg.message_type != "text":
                reply_text(client, message_id, "请发送包含视频/音频链接的文本消息")
                return

            # 解析消息内容
            content = json.loads(msg.content)
            text = content.get("text", "")
            urls = extract_urls(text)

            if not urls:
                reply_text(client, message_id, "未检测到链接，请发送包含视频/音频 URL 的消息")
                return

            # 解析指令
            need_text, need_summary, is_long = parse_command(text)
            url = urls[0]

            # 构建提示信息
            parts = []
            if is_long:
                parts.append("长视频模式")
            if need_text:
                parts.append("转写原文")
            if need_summary:
                parts.append("AI 总结")
            mode = " + ".join(parts) if parts else "全部"
            reply_text(client, message_id, f"收到链接，正在处理（{mode}）...\n{url}")

            # 子线程执行管线
            thread = threading.Thread(
                target=run_pipeline,
                args=(client, message_id, url, config, need_text, need_summary, is_long),
                daemon=True,
            )
            thread.start()

        except Exception as e:
            print(f"[事件] 处理消息异常: {e}")

    return on_message


# ── 二维码 ────────────────────────────────────────────────


def print_qrcode(app_id):
    """在终端打印添加机器人的二维码。"""
    url = f"https://applink.feishu.cn/client/bot/add?appId={app_id}"
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    print(f"\n扫码添加机器人，或手动访问:\n{url}\n")


# ── 配置引导 ──────────────────────────────────────────────


def interactive_setup(config):
    """交互式引导用户完成首次配置。"""
    from app import save_config
    changed = False

    # 飞书配置
    feishu_cfg = config.get("feishu", {})
    if not feishu_cfg.get("app_id") or not feishu_cfg.get("app_secret"):
        print("\n=== 飞书机器人配置 ===")
        print("请在 https://open.feishu.cn/app 创建应用并获取凭证")
        app_id = input("App ID: ").strip()
        app_secret = input("App Secret: ").strip()
        if app_id and app_secret:
            config["feishu"] = {"app_id": app_id, "app_secret": app_secret}
            changed = True

    # LLM 配置
    backend = config.get("llm_backend", "")
    custom_api = config.get("custom_api", {})
    if backend == "custom_api" and not custom_api.get("base_url"):
        print("\n=== 大模型 API 配置 ===")
        print("支持任何 OpenAI 兼容 API（DeepSeek、通义千问、OpenAI 等）")
        base_url = input("API Base URL: ").strip()
        api_key = input("API Key: ").strip()
        model = input("Model 名称: ").strip()
        if base_url:
            config["custom_api"] = {
                "format": "openai",
                "base_url": base_url,
                "api_key": api_key,
                "model": model,
            }
            changed = True

    if changed:
        save_config(config)
        print(f"\n配置已保存到 {os.path.expanduser('~/.funasr_config.json')}\n")

    return config


# ── 主入口 ────────────────────────────────────────────────


def main():
    config = load_config()

    # 检测配置是否完整，不完整则引导配置
    feishu_cfg = config.get("feishu", {})
    need_setup = (
        not feishu_cfg.get("app_id")
        or not feishu_cfg.get("app_secret")
        or (config.get("llm_backend") == "custom_api"
            and not config.get("custom_api", {}).get("base_url"))
    )
    if need_setup:
        config = interactive_setup(config)

    app_id = config["feishu"]["app_id"]
    app_secret = config["feishu"]["app_secret"]

    # 打印二维码
    print_qrcode(app_id)

    # 构建客户端
    client = build_client(app_id, app_secret)

    # 创建事件处理器
    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(make_event_handler(client, config))
        .build()
    )

    # 启动 WebSocket 长连接
    ws_client = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )

    print("飞书机器人已启动，等待消息...")
    ws_client.start()


if __name__ == "__main__":
    main()
