import argparse
import json
import os

import pytest

import transcribe_url


def test_parse_batch_file_skips_empty_lines_and_comments(tmp_path):
    batch_file = tmp_path / "tasks.txt"
    batch_file.write_text(
        "\n"
        "# first comment\n"
        "  https://example.com/video  \n"
        "\n"
        "C:/media/sample.mp4\n"
        "   # indented comment\n",
        encoding="utf-8",
    )

    assert transcribe_url.parse_batch_file(str(batch_file)) == [
        "https://example.com/video",
        "C:/media/sample.mp4",
    ]


def test_collect_input_dir_tasks_keeps_supported_media_sorted(tmp_path):
    (tmp_path / "b.mp4").write_text("video", encoding="utf-8")
    (tmp_path / "a.m4a").write_text("audio", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")
    (tmp_path / "folder.wav").mkdir()

    tasks = transcribe_url.collect_input_dir_tasks(str(tmp_path))

    assert [os.path.basename(path) for path in tasks] == ["a.m4a", "b.mp4"]


def test_collect_input_dir_tasks_rejects_missing_directory(tmp_path):
    with pytest.raises(ValueError, match="input directory does not exist"):
        transcribe_url.collect_input_dir_tasks(str(tmp_path / "missing"))


def test_unique_output_pair_adds_shared_suffix_when_either_file_exists(tmp_path):
    first_txt, first_md = transcribe_url.unique_output_pair(str(tmp_path), "Talk")
    assert first_txt.endswith("Talk_transcription.txt")
    assert first_md.endswith("Talk_summary.md")

    (tmp_path / "Talk_transcription.txt").write_text("old", encoding="utf-8")
    second_txt, second_md = transcribe_url.unique_output_pair(str(tmp_path), "Talk")
    assert second_txt.endswith("Talk_2_transcription.txt")
    assert second_md.endswith("Talk_2_summary.md")

    (tmp_path / "Talk_2_summary.md").write_text("old", encoding="utf-8")
    third_txt, third_md = transcribe_url.unique_output_pair(str(tmp_path), "Talk")
    assert third_txt.endswith("Talk_3_transcription.txt")
    assert third_md.endswith("Talk_3_summary.md")


def test_process_batch_item_writes_txt_and_md(monkeypatch, tmp_path):
    monkeypatch.setattr(
        transcribe_url,
        "_prepare_batch_input",
        lambda *args, **kwargs: (
            "input.wav",
            {"title": "Demo Talk", "duration": 65, "webpage_url": "https://example.com/demo"},
        ),
    )

    import echoscribe.core.llm as core_llm
    import echoscribe.core.storage as core_storage
    import echoscribe.core.transcribe as core_transcribe

    monkeypatch.setattr(core_storage, "safe_title", lambda title: title.replace(" ", "_"))
    monkeypatch.setattr(
        core_transcribe,
        "transcribe_audio",
        lambda _path: {
            "sentences": [{"start": "00:00:01", "end": "00:00:03", "text": "hello"}],
            "full_text": "hello",
        },
    )
    monkeypatch.setattr(core_llm, "summarize_with_llm", lambda text, prompt_type: "## Summary")

    item = transcribe_url.process_batch_item("https://example.com/demo", str(tmp_path))

    assert item["status"] == "success"
    assert item["title"] == "Demo Talk"
    assert os.path.exists(item["transcription_file"])
    assert os.path.exists(item["summary_file"])
    assert "hello" in open(item["transcription_file"], encoding="utf-8").read()
    assert "## Summary" in open(item["summary_file"], encoding="utf-8").read()


def test_process_batch_item_keeps_txt_when_summary_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(
        transcribe_url,
        "_prepare_batch_input",
        lambda *args, **kwargs: ("input.wav", {"title": "Demo", "duration": None}),
    )

    import echoscribe.core.llm as core_llm
    import echoscribe.core.storage as core_storage
    import echoscribe.core.transcribe as core_transcribe

    monkeypatch.setattr(core_storage, "safe_title", lambda title: title)
    monkeypatch.setattr(
        core_transcribe,
        "transcribe_audio",
        lambda _path: {"sentences": [], "full_text": "plain transcript"},
    )

    def fail_summary(_text, _prompt_type):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(core_llm, "summarize_with_llm", fail_summary)

    item = transcribe_url.process_batch_item("https://example.com/demo", str(tmp_path))

    assert item["status"] == "summary_failed"
    assert "llm unavailable" in item["error"]
    assert os.path.exists(item["transcription_file"])
    assert item["summary_file"] is None


def test_run_batch_continues_and_writes_report(monkeypatch, tmp_path):
    batch_file = tmp_path / "tasks.txt"
    batch_file.write_text("ok\nsummary-fail\nfail\n", encoding="utf-8")
    output_dir = tmp_path / "out"

    items = {
        "ok": {
            "input": "ok",
            "status": "success",
            "title": "OK",
            "duration_seconds": 1,
            "transcription_file": "ok.txt",
            "summary_file": "ok.md",
            "error": None,
        },
        "summary-fail": {
            "input": "summary-fail",
            "status": "summary_failed",
            "title": "Summary Fail",
            "duration_seconds": 1,
            "transcription_file": "summary.txt",
            "summary_file": None,
            "error": "llm unavailable",
        },
        "fail": {
            "input": "fail",
            "status": "failed",
            "title": None,
            "duration_seconds": 1,
            "transcription_file": None,
            "summary_file": None,
            "error": "download failed",
        },
    }
    calls = []

    def fake_process(task, *args, **kwargs):
        calls.append(task)
        return dict(items[task])

    monkeypatch.setattr(transcribe_url, "process_batch_item", fake_process)
    args = argparse.Namespace(
        batch=str(batch_file),
        output_dir=str(output_dir),
        prompt_type="default",
        cookies=None,
        part=None,
        proxy=None,
    )

    exit_code = transcribe_url.run_batch(args)

    assert exit_code == 1
    assert calls == ["ok", "summary-fail", "fail"]
    report = json.loads((output_dir / "batch_report.json").read_text(encoding="utf-8"))
    assert report["summary"] == {"success": 1, "summary_failed": 1, "failed": 1}
    assert [item["status"] for item in report["items"]] == [
        "success",
        "summary_failed",
        "failed",
    ]


def test_run_batch_accepts_input_dir(monkeypatch, tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "clip.mp4").write_text("video", encoding="utf-8")
    output_dir = tmp_path / "out"
    calls = []

    def fake_process(task, *args, **kwargs):
        calls.append(task)
        return {
            "input": task,
            "status": "success",
            "title": "Clip",
            "duration_seconds": 1,
            "transcription_file": "clip.txt",
            "summary_file": "clip.md",
            "error": None,
        }

    monkeypatch.setattr(transcribe_url, "process_batch_item", fake_process)
    args = argparse.Namespace(
        batch=None,
        input_dir=str(media_dir),
        output_dir=str(output_dir),
        prompt_type="default",
        cookies=None,
        part=None,
        proxy=None,
    )

    exit_code = transcribe_url.run_batch(args)

    assert exit_code == 0
    assert calls == [str(media_dir / "clip.mp4")]
    report = json.loads((output_dir / "batch_report.json").read_text(encoding="utf-8"))
    assert report["source_type"] == "input_dir"
    assert report["batch_file"] is None
    assert report["input_dir"] == str(media_dir.resolve())


def test_parser_keeps_single_url_mode_compatible():
    args = transcribe_url.build_parser().parse_args(
        ["https://example.com/video", "-o", "output.txt", "--device", "cpu"]
    )

    assert args.url == "https://example.com/video"
    assert args.output == "output.txt"
    assert args.batch is None
    assert args.device == "cpu"


def test_parser_accepts_input_dir_mode():
    args = transcribe_url.build_parser().parse_args(
        ["--input-dir", "downloads", "--output-dir", "docs"]
    )

    assert args.input_dir == "downloads"
    assert args.output_dir == "docs"
    assert args.batch is None
    assert args.url is None


def test_main_rejects_batch_with_single_output():
    with pytest.raises(SystemExit):
        transcribe_url.main(["--batch", "tasks.txt", "-o", "out.txt"])


def test_main_rejects_input_dir_with_batch():
    with pytest.raises(SystemExit):
        transcribe_url.main(["--batch", "tasks.txt", "--input-dir", "downloads"])
