#!/usr/bin/env python3
"""Command-line transcription for one URL or a blocking batch of jobs."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time


AUDIO_VIDEO_EXTENSIONS = {
    ".aac",
    ".avi",
    ".flac",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
    ".wmv",
}


def check_deps():
    """Exit unless ffmpeg is available."""
    if not shutil.which("ffmpeg"):
        print("Error: ffmpeg was not found. Install ffmpeg and ensure it is on PATH.")
        print("  Download: https://ffmpeg.org/download.html")
        sys.exit(1)


def get_proxy():
    """Read proxy URL from env. Priority: YT_PROXY > HTTPS_PROXY > HTTP_PROXY."""
    return os.environ.get("YT_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")


def download_audio(url, output_dir, cookies=None, part=None, proxy=None):
    """Legacy single-URL yt-dlp path. Returns (wav_path, metadata)."""
    import yt_dlp

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

    effective_proxy = proxy or get_proxy()
    if effective_proxy:
        ydl_opts["proxy"] = effective_proxy
    if cookies:
        ydl_opts["cookiefile"] = cookies
    if part is not None:
        ydl_opts["playlist_items"] = str(part)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        err_msg = str(e).lower()
        print(f"Error: download failed - {url}")
        print(f"  Reason: {e}")
        if "login" in err_msg or "cookie" in err_msg or "fresh" in err_msg:
            cookie_path = os.path.expanduser("~/.funasr_cookies/douyin_cookies.txt")
            print()
            print("  Browser cookies failed. Recommended manual cookie export:")
            print("  1. Install the browser extension 'Get cookies.txt LOCALLY'.")
            print("  2. Open and log in to the source site in your browser.")
            print("  3. Export cookies to:")
            print(f"     {cookie_path}")
            print("  4. Run this command again, or pass --cookies with another file path.")
        sys.exit(1)
    except yt_dlp.utils.ExtractorError as e:
        print(f"Error: could not parse URL - {url}")
        print(f"  Reason: {e}")
        sys.exit(1)

    metadata = {
        "title": info.get("title", "unknown_title"),
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
                if ext in ("m4a", "mp3", "flac", "ogg", "opus", "webm"):
                    src = os.path.join(output_dir, f)
                    wav_path = os.path.join(output_dir, f"{video_id}.wav")
                    convert_to_wav(src, wav_path)
                    break
            else:
                print("Error: download completed but no audio file was found.")
                sys.exit(1)

    return wav_path, metadata


def convert_to_wav(input_path, output_path):
    """Convert audio to the 16 kHz mono WAV format expected by FunASR."""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print("Error: audio conversion failed")
        print(f"  Reason: {e.stderr.decode(errors='replace') if e.stderr else str(e)}")
        sys.exit(1)


def transcribe(audio_path, model_name="paraformer-zh", device="auto", batch_size_s=300):
    """Legacy single-file FunASR transcription path."""
    import torch
    from funasr import AutoModel

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"

    print(f"Loading model (device: {device})...")
    try:
        model = AutoModel(
            model=model_name,
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            device=device,
        )
    except RuntimeError as e:
        if "out of memory" in str(e).lower() and device == "cuda":
            print("Warning: GPU memory is insufficient, falling back to CPU...")
            device = "cpu"
            model = AutoModel(
                model=model_name,
                vad_model="fsmn-vad",
                punc_model="ct-punc",
                device=device,
            )
        else:
            print(f"Error: model loading failed - {e}")
            sys.exit(1)

    print("Transcribing...")
    start_time = time.time()
    try:
        results = model.generate(
            input=audio_path,
            batch_size_s=batch_size_s,
            sentence_timestamp=True,
        )
    except RuntimeError as e:
        if "out of memory" in str(e).lower() and device == "cuda":
            print("Warning: GPU memory is insufficient, retrying on CPU...")
            device = "cpu"
            model = AutoModel(
                model=model_name,
                vad_model="fsmn-vad",
                punc_model="ct-punc",
                device=device,
            )
            results = model.generate(
                input=audio_path,
                batch_size_s=batch_size_s,
                sentence_timestamp=True,
            )
        else:
            print(f"Error: transcription failed - {e}")
            sys.exit(1)

    elapsed = time.time() - start_time
    print(f"Transcription completed in {elapsed:.1f}s")
    return results


def format_timestamp(ms):
    """Convert milliseconds to HH:MM:SS."""
    seconds = int(ms / 1000)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_duration(seconds):
    """Convert seconds to MM:SS or HH:MM:SS."""
    if seconds is None:
        return "unknown"
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_output(results, metadata, original_url):
    """Format legacy FunASR raw results as readable timestamped text."""
    lines = []
    sep = "=" * 40

    lines.append(sep)
    lines.append(f"Title: {metadata['title']}")
    lines.append(f"URL: {metadata['webpage_url']}")
    lines.append(f"Duration: {format_duration(metadata['duration'])}")
    lines.append(sep)
    lines.append("")

    full_text_parts = []

    for result in results:
        sentence_info = result.get("sentence_info", [])
        if sentence_info:
            for sent in sentence_info:
                text = sent.get("text", "")
                start = sent.get("start", 0)
                end = sent.get("end", 0)
                ts_start = format_timestamp(start)
                ts_end = format_timestamp(end)
                lines.append(f"[{ts_start} - {ts_end}] {text}")
                full_text_parts.append(text)
        else:
            text = result.get("text", "")
            if text:
                lines.append(text)
                full_text_parts.append(text)

    if full_text_parts:
        lines.append("")
        lines.append(sep)
        lines.append("Full text:")
        lines.append("".join(full_text_parts))
        lines.append(sep)

    return "\n".join(lines)


def parse_batch_file(batch_path):
    """Return non-empty, non-comment lines from a batch task file."""
    tasks = []
    with open(batch_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            tasks.append(line)
    return tasks


def collect_input_dir_tasks(input_dir):
    """Return sorted media file paths from a directory."""
    root = os.path.abspath(os.path.expanduser(input_dir))
    if not os.path.isdir(root):
        raise ValueError(f"input directory does not exist: {input_dir}")

    tasks = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext in AUDIO_VIDEO_EXTENSIONS:
            tasks.append(path)
    return tasks


def unique_output_pair(output_dir, base_name):
    """Return unique transcription/summary output paths sharing one suffix."""
    stem = base_name
    index = 1
    while True:
        suffix = "" if index == 1 else f"_{index}"
        txt_path = os.path.join(output_dir, f"{stem}{suffix}_transcription.txt")
        md_path = os.path.join(output_dir, f"{stem}{suffix}_summary.md")
        if not os.path.exists(txt_path) and not os.path.exists(md_path):
            return txt_path, md_path
        index += 1


def format_core_transcription(transcription, metadata, source):
    """Format echoscribe.core transcription output as timestamped text."""
    lines = []
    sep = "=" * 40
    title = metadata.get("title") or "untitled"
    source_url = metadata.get("webpage_url") or source

    lines.append(sep)
    lines.append(f"Title: {title}")
    lines.append(f"Source: {source_url}")
    lines.append(f"Duration: {format_duration(metadata.get('duration'))}")
    lines.append(sep)
    lines.append("")

    sentences = transcription.get("sentences", [])
    for sent in sentences:
        start = sent.get("start", "00:00:00")
        end = sent.get("end", "00:00:00")
        text = sent.get("text", "")
        lines.append(f"[{start} - {end}] {text}")

    full_text = transcription.get("full_text", "")
    if full_text:
        lines.append("")
        lines.append(sep)
        lines.append("Full text:")
        lines.append(full_text)
        lines.append(sep)

    return "\n".join(lines)


def resolve_output_dir(output_dir):
    """Resolve the batch output directory, defaulting to configured docs_dir."""
    if output_dir:
        resolved = os.path.abspath(output_dir)
        os.makedirs(resolved, exist_ok=True)
        return resolved

    from echoscribe.core.storage import get_docs_dir

    return get_docs_dir()


def _prepare_batch_input(task, tmpdir, cookies=None, part=None, proxy=None):
    """Return (wav_path, metadata) for a URL or local media path."""
    from echoscribe.core.audio import convert_to_wav as core_convert_to_wav
    from echoscribe.core.downloaders import download_audio as core_download_audio

    local_path = os.path.abspath(os.path.expanduser(task))
    if os.path.exists(local_path):
        wav_path = os.path.join(tmpdir, "input.wav")
        core_convert_to_wav(local_path, wav_path)
        metadata = {
            "title": os.path.splitext(os.path.basename(local_path))[0],
            "duration": None,
            "id": "",
            "webpage_url": local_path,
        }
        return wav_path, metadata

    wav_path, _video_path, metadata = core_download_audio(
        task,
        tmpdir,
        cookies=cookies,
        part=part,
        proxy=proxy,
    )
    return wav_path, metadata


def process_batch_item(task, output_dir, prompt_type="default", cookies=None, part=None, proxy=None):
    """Process one batch item and return its report entry."""
    from echoscribe.core.llm import summarize_with_llm
    from echoscribe.core.storage import safe_title
    from echoscribe.core.transcribe import transcribe_audio

    item_start = time.time()
    report = {
        "input": task,
        "status": "failed",
        "title": None,
        "duration_seconds": None,
        "transcription_file": None,
        "summary_file": None,
        "error": None,
    }

    tmpdir = tempfile.mkdtemp(prefix="funasr_batch_")
    try:
        wav_path, metadata = _prepare_batch_input(task, tmpdir, cookies=cookies, part=part, proxy=proxy)
        title = metadata.get("title") or "untitled"
        report["title"] = title

        print("  Transcribing...")
        transcription = transcribe_audio(wav_path)

        base_name = safe_title(title)
        txt_path, md_path = unique_output_pair(output_dir, base_name)
        transcription_text = format_core_transcription(transcription, metadata, task)
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(transcription_text)
        report["transcription_file"] = txt_path

        print("  Summarizing...")
        try:
            summary = summarize_with_llm(transcription.get("full_text", ""), prompt_type)
        except Exception as e:
            report["status"] = "summary_failed"
            report["error"] = str(e)
            return report

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# {title} - AI Summary\n\n{summary}\n")
        report["summary_file"] = md_path
        report["status"] = "success"
        return report
    except Exception as e:
        report["status"] = "failed"
        report["error"] = str(e)
        return report
    finally:
        report["duration_seconds"] = round(time.time() - item_start, 2)
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_batch(args):
    """Run batch jobs sequentially and return a process exit code."""
    input_dir = getattr(args, "input_dir", None)
    batch_file = getattr(args, "batch", None)

    if input_dir:
        try:
            tasks = collect_input_dir_tasks(input_dir)
        except ValueError as e:
            print(f"Error: {e}")
            return 1
        source_label = os.path.abspath(input_dir)
    else:
        tasks = parse_batch_file(batch_file)
        source_label = os.path.abspath(batch_file)

    if not tasks:
        if input_dir:
            print(f"No supported media files found in input directory: {input_dir}")
        else:
            print(f"No tasks found in batch file: {batch_file}")
        return 1

    output_dir = resolve_output_dir(args.output_dir)
    print(f"Batch tasks: {len(tasks)}")
    print(f"Output directory: {output_dir}")

    report = {
        "source": source_label,
        "source_type": "input_dir" if input_dir else "batch_file",
        "batch_file": os.path.abspath(batch_file) if batch_file else None,
        "input_dir": os.path.abspath(input_dir) if input_dir else None,
        "output_dir": output_dir,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "items": [],
        "summary": {"success": 0, "summary_failed": 0, "failed": 0},
    }

    for idx, task in enumerate(tasks, start=1):
        print(f"[{idx}/{len(tasks)}] Processing: {task}")
        item = process_batch_item(
            task,
            output_dir,
            prompt_type=args.prompt_type,
            cookies=args.cookies,
            part=args.part,
            proxy=args.proxy,
        )
        report["items"].append(item)
        report["summary"][item["status"]] = report["summary"].get(item["status"], 0) + 1
        if item["status"] == "success":
            print(f"  OK: {item['transcription_file']} | {item['summary_file']}")
        elif item["status"] == "summary_failed":
            print(f"  Summary failed; transcription saved: {item['transcription_file']}")
            print(f"  Error: {item['error']}")
        else:
            print(f"  Failed: {item['error']}")

    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    report_path = os.path.join(output_dir, "batch_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print()
    print("Batch complete")
    print(f"  success: {report['summary'].get('success', 0)}")
    print(f"  summary_failed: {report['summary'].get('summary_failed', 0)}")
    print(f"  failed: {report['summary'].get('failed', 0)}")
    print(f"  report: {report_path}")
    incomplete = report["summary"].get("failed", 0) + report["summary"].get("summary_failed", 0)
    return 0 if incomplete == 0 else 1


def run_single(args):
    """Run the legacy single-URL mode."""
    tmpdir = tempfile.mkdtemp(prefix="funasr_transcribe_")
    try:
        print(f"Downloading: {args.url}")
        wav_path, metadata = download_audio(args.url, tmpdir, args.cookies, args.part, args.proxy)
        print(f"Download complete: {metadata['title']}")

        if args.save_audio:
            shutil.copy2(wav_path, args.save_audio)
            print(f"Audio saved to: {args.save_audio}")

        results = transcribe(wav_path, args.model, args.device, args.batch_size)
        output_text = format_output(results, metadata, args.url)

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output_text)
            print(f"Transcription saved to: {args.output}")
        else:
            print()
            print(output_text)
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Download/transcribe media from a URL, or process a blocking batch list."
    )
    parser.add_argument("url", nargs="?", help="Video/audio URL for single-item mode")
    parser.add_argument("-o", "--output", help="Single-item output txt path")
    parser.add_argument("--batch", help="Batch task file; each non-comment line is a URL or local media path")
    parser.add_argument("--input-dir", help="Directory of local audio/video files to process in batch mode")
    parser.add_argument("--output-dir", help="Batch output directory; defaults to configured docs_dir")
    parser.add_argument("--prompt-type", default="default", choices=["default", "long"],
                        help="Summary prompt type for batch mode")
    parser.add_argument("--model", default="paraformer-zh", help="ASR model for single-item mode")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"],
                        help="Compute device for single-item mode")
    parser.add_argument("--batch-size", type=int, default=300,
                        help="VAD batch size in seconds for single-item mode")
    parser.add_argument("--cookies", help="Cookie file for sources that require login")
    parser.add_argument("--proxy", help="Proxy URL, e.g. socks5://host:port")
    parser.add_argument("--part", type=int, help="Playlist item number for supported sources")
    parser.add_argument("--save-audio", help="Save downloaded audio copy in single-item mode")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    batch_modes = sum(1 for value in (args.batch, args.input_dir) if value)
    if args.url and batch_modes:
        parser.error("url, --batch, and --input-dir are mutually exclusive")
    if batch_modes > 1:
        parser.error("--batch and --input-dir are mutually exclusive")
    if not args.url and batch_modes == 0:
        parser.error("provide a URL, --batch tasks.txt, or --input-dir media_dir")
    if args.output and batch_modes:
        parser.error("-o/--output is only valid for single-item mode; use --output-dir for batches")
    if args.save_audio and batch_modes:
        parser.error("--save-audio is only valid for single-item mode")

    check_deps()
    if args.batch or args.input_dir:
        return run_batch(args)
    return run_single(args)


if __name__ == "__main__":
    raise SystemExit(main())
