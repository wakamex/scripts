from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import speech_to_text


def test_render_parakeet_segments_with_timestamps() -> None:
    lines = speech_to_text.render_segments(
        [
            {"start": 1.25, "end": 3.5, "text": "First sentence."},
            {
                "start": 4.0,
                "end": 7.75,
                "text": "This second sentence is long enough to close the grouped paragraph cleanly.",
            },
        ],
        timestamps=True,
    )

    assert lines == [
        "[1.2s -> 7.8s] First sentence. This second sentence is long enough to close the grouped paragraph cleanly."
    ]


def test_render_parakeet_segments_without_timestamps() -> None:
    lines = speech_to_text.render_segments(
        [{"start": 1.0, "end": 2.0, "text": "A local transcript."}],
        timestamps=False,
    )

    assert lines == ["A local transcript."]


def test_parakeet_requires_exclusive_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        speech_to_text.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="3341630, 3150, /code/transcribe/.venv/bin/python3\n"
        ),
    )

    with pytest.raises(RuntimeError, match="exclusive GPU access") as error:
        speech_to_text.require_exclusive_cuda()

    assert "PID 3341630" in str(error.value)
    assert "3150 MiB" in str(error.value)


def test_parakeet_accepts_idle_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        speech_to_text.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=""),
    )

    speech_to_text.require_exclusive_cuda()


def test_parakeet_runtime_removes_conflicting_library_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    monkeypatch.setenv("LD_LIBRARY_PATH", "/incompatible/cudnn")
    monkeypatch.setenv("VIRTUAL_ENV", "/temporary/script-environment")
    monkeypatch.setattr(sys, "argv", ["speech_to_text.py", "audio.wav"])

    def fake_execvpe(executable: str, command: list[str], environment: dict[str, str]) -> None:
        captured.update(
            executable=executable,
            command=command,
            environment=environment,
        )

    monkeypatch.setattr(speech_to_text.os, "execvpe", fake_execvpe)

    speech_to_text.restart_with_parakeet_runtime()

    assert captured["executable"] == "uv"
    assert captured["command"][:5] == [
        "uv",
        "run",
        "--project",
        "/code/transcribe",
        "--extra",
    ]
    assert captured["command"][-1] == "--parakeet-runtime-ready"
    assert "LD_LIBRARY_PATH" not in captured["environment"]
    assert "VIRTUAL_ENV" not in captured["environment"]
    assert Path(captured["command"][7]).resolve() == Path(speech_to_text.__file__).resolve()


def test_local_whisper_runtime_uses_system_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    monkeypatch.setenv("VIRTUAL_ENV", "/temporary/script-environment")
    monkeypatch.setattr(sys, "argv", ["speech_to_text.py", "audio.wav"])

    def fake_execve(executable: str, command: list[str], environment: dict[str, str]) -> None:
        captured.update(
            executable=executable,
            command=command,
            environment=environment,
        )

    monkeypatch.setattr(speech_to_text.os, "execve", fake_execve)

    speech_to_text.restart_with_local_whisper_runtime()

    assert captured["executable"] == "/usr/bin/python3"
    assert captured["command"][-1] == "--local-whisper-runtime-ready"
    assert "VIRTUAL_ENV" not in captured["environment"]


def test_local_whisper_rejects_speaker_diarization(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "speech_to_text.py",
            "audio.wav",
            "--engine",
            "whisper-large-v3",
            "--speakers",
        ],
    )

    with pytest.raises(SystemExit, match="2"):
        speech_to_text.main()

    assert "--speakers is not supported" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("extra_args", "message"),
    [
        (["--speakers"], "--speakers is not supported"),
        (["--language", "fr"], "supports English only"),
    ],
)
def test_parakeet_rejects_incompatible_options(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra_args: list[str],
    message: str,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["speech_to_text.py", "audio.wav", "--engine", "parakeet", *extra_args],
    )

    with pytest.raises(SystemExit, match="2"):
        speech_to_text.main()

    assert message in capsys.readouterr().err
