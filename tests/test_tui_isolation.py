"""The TUI is an opt-in extra: it must never affect the CLI or its dependency footprint."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import compress_mov.tui as tui_package

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())


def _python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)


# --- packaging -----------------------------------------------------------------


def test_textual_is_not_a_core_dependency() -> None:
    core = PYPROJECT["project"]["dependencies"]

    assert not any(dep.lower().startswith("textual") for dep in core)


def test_textual_is_offered_as_the_tui_extra() -> None:
    extras = PYPROJECT["project"]["optional-dependencies"]

    assert any(dep.lower().startswith("textual") for dep in extras["tui"])


def test_separate_entry_scripts_for_cli_and_tui() -> None:
    scripts = PYPROJECT["project"]["scripts"]

    assert scripts["compress-mov"] == "compress_mov.cli:run"
    assert scripts["compress-mov-tui"] == "compress_mov.tui:run"


# --- the CLI never loads the TUI ------------------------------------------------


@pytest.mark.parametrize("module", ["compress_mov.cli", "compress_mov", "compress_mov.__main__", "compress_mov.encode"])
def test_cli_side_modules_import_neither_textual_nor_the_tui_package(module: str) -> None:
    proc = _python(
        f"import sys, {module}\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] == 'textual' or m.startswith('compress_mov.tui'))\n"
        "print(','.join(bad))\n"
        "sys.exit(1 if bad else 0)"
    )

    assert proc.returncode == 0, f"{module} pulled in: {proc.stdout.strip()} {proc.stderr.strip()}"


def test_cli_still_works_when_textual_is_not_installed() -> None:
    """Simulate a base install: importing textual raises, and the CLI must not care."""
    proc = _python(
        "import sys\n"
        "sys.modules['textual'] = None\n"  # any `import textual` now raises ModuleNotFoundError
        "from typer.testing import CliRunner\n"
        "from compress_mov.cli import app\n"
        "r = CliRunner().invoke(app, ['--help'])\n"
        "assert r.exit_code == 0 and 'HEVC' in r.output, r.output\n"
        "print('ok')"
    )

    assert proc.returncode == 0 and "ok" in proc.stdout, proc.stderr


def test_cli_help_does_not_mention_the_tui() -> None:
    from typer.testing import CliRunner

    from compress_mov.cli import app

    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "tui" not in result.output.lower()


def test_tui_reuses_the_encoder_without_installing_cli_signal_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The TUI owns the terminal, so it must not register the CLI's SIGINT/SIGTERM handlers."""
    import signal

    installed: list[int] = []
    monkeypatch.setattr(signal, "signal", lambda signum, handler: installed.append(signum))

    import importlib

    pytest.importorskip("textual")
    importlib.reload(importlib.import_module("compress_mov.tui.app"))

    assert installed == []


# --- the entry script -----------------------------------------------------------


def test_entry_script_explains_the_extra_when_textual_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "textual", None)
    monkeypatch.delitem(sys.modules, "compress_mov.tui.app", raising=False)

    with pytest.raises(SystemExit) as exc:
        tui_package.run([])

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "compress-mov[tui]" in err and "uv tool install" in err


def test_entry_script_help_works_without_textual(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "textual", None)
    monkeypatch.delitem(sys.modules, "compress_mov.tui.app", raising=False)

    with pytest.raises(SystemExit) as exc:
        tui_package.run(["--help"])

    assert exc.value.code == 0
    assert "compress-mov-tui" in capsys.readouterr().out


def test_entry_script_does_not_hide_unrelated_import_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a missing *textual* is the friendly path; a genuine bug in our code must still surface."""
    monkeypatch.delitem(sys.modules, "compress_mov.tui.app", raising=False)
    monkeypatch.setitem(sys.modules, "compress_mov.bins", None)  # app.py imports it -> ModuleNotFoundError

    with pytest.raises(ModuleNotFoundError):
        tui_package.run([])


def test_entry_script_runs_the_app_and_propagates_its_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("textual")
    from compress_mov.tui import app as app_module

    seen: dict[str, object] = {}

    class FakeApp:
        return_code = 3

        def __init__(self, args: object) -> None:
            seen["args"] = args

        def run(self) -> None:
            seen["ran"] = True

    monkeypatch.setattr(app_module, "CompressApp", FakeApp)

    with pytest.raises(SystemExit) as exc:
        tui_package.run(["-r", "x.mov"])

    assert exc.value.code == 3 and seen["ran"] is True
    assert seen["args"].recursive is True  # type: ignore[attr-defined]
