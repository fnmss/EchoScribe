"""ASR pipeline: long-audio splitting, per-chunk transcription, sentence grouping.

Adds runtime CUDA OOM recovery on top of the load-time recovery in
``model.py``: if ``model.generate`` blows up with OOM, we drop the
GPU singleton, re-create it on CPU, and retry the failing chunk.
Subsequent chunks then run on CPU as well.
"""
import os

from . import model as _model
from .audio import format_timestamp, split_audio


def group_sentences_by_interval(sentences, interval_ms=120000):
    """Bucket sentences into ``interval_ms`` windows for the UI's time-grouped view.

    Each input sentence must have ``_start_ms``, ``text``, ``end``.
    Each output group has: ``start``, ``end``, ``start_ms``, ``text``,
    ``sentence_count`` — the shape consumed by ``static/app.js``.
    """
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


def _generate_with_oom_fallback(audio_path):
    """Run model.generate with CUDA OOM → CPU retry."""
    m = _model.get_model()
    try:
        return m.generate(input=audio_path, batch_size_s=300, sentence_timestamp=True)
    except RuntimeError as e:
        if "out of memory" not in str(e).lower():
            raise
        print("[transcribe] CUDA OOM during generate, reloading on CPU and retrying...")
        _model.reset_model()
        m = _model.get_model(force_device="cpu")
        return m.generate(input=audio_path, batch_size_s=300, sentence_timestamp=True)


def _transcribe_chunk(audio_path):
    """Transcribe a single chunk; return (sentences, full_text_parts)."""
    results = _generate_with_oom_fallback(audio_path)
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
    """Transcribe ``audio_path``, auto-splitting long files. Returns dict with:
        - sentences: per-sentence list (text, start "HH:MM:SS", end "HH:MM:SS")
        - grouped: 2-minute time-grouped buckets for the UI
        - full_text: concatenated plain text
    """
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

        # Clean up temp chunk file (but never the original)
        if chunk_path != audio_path and os.path.exists(chunk_path):
            try:
                os.remove(chunk_path)
            except OSError:
                pass

    # Clean up the chunk dir created by split_audio
    if len(chunks) > 1:
        chunk_dir = os.path.dirname(chunks[0][0])
        try:
            os.rmdir(chunk_dir)
        except OSError:
            pass

    grouped = group_sentences_by_interval(all_sentences)

    # _start_ms is internal; strip before returning
    for s in all_sentences:
        s.pop("_start_ms", None)

    return {
        "sentences": all_sentences,
        "grouped": grouped,
        "full_text": "".join(all_full_text),
    }
