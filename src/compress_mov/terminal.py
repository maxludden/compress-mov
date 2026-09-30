"""Relaunching in a real terminal for Finder Quick Action use.

A Quick Action has no terminal, so when it's clearly running headless the
CLI reopens itself in Terminal.app (or $COMPRESS_MOV_TERMINAL, e.g. iTerm)
before doing anything else.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from .bins import OPEN


def should_relaunch() -> bool:
    """True when we look like a Finder Quick Action rather than a shell.

    Relaunching opens a GUI window and returns immediately, so it must not
    fire for merely redirected output (``2>log``), ssh sessions or non-macOS
    hosts. Requires all of stdin/stdout/stderr to be non-TTY, macOS, no
    ``SSH_CONNECTION``, and no ``COMPRESS_MOV_NO_RELAUNCH`` opt-out.
    """
    if sys.platform != "darwin":
        return False
    env = os.environ
    if "COMPRESS_MOV_LAUNCHED" in env or "COMPRESS_MOV_NO_RELAUNCH" in env or "SSH_CONNECTION" in env:
        return False
    return not any(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr))


def self_command() -> list[str]:
    """Command that re-runs this program, however it was started.

    ``python -m compress_mov`` leaves ``sys.argv[0]`` pointing at a
    non-executable ``__main__.py``, so the launcher goes through the
    interpreter instead of trusting argv[0].
    """
    return [sys.executable, "-m", "compress_mov"]


def build_launcher_script(command: list[str], args: list[str], tmp_dir: Path) -> str:
    """zsh source for the ``.command`` launcher."""
    quoted_cmd = " ".join(shlex.quote(c) for c in [*command, *args])
    return textwrap.dedent(f"""\
        #!/bin/zsh
        rm -rf -- {shlex.quote(str(tmp_dir))}
        COMPRESS_MOV_LAUNCHED=1 {quoted_cmd}
        print
        read -k1 "?Done — press any key to close…"
        exit
        """)


def relaunch_in_terminal(argv: list[str], command: list[str] | None = None) -> None:
    """Reopen the CLI with `argv` inside a Terminal.app window.

    Writes a `.command` launcher to a scratch dir (sidesteps AppleScript
    string escaping entirely) and hands it to `open -a <app>`. The
    launcher deletes its own directory, runs `command` (default: this
    interpreter via ``-m compress_mov``) with COMPRESS_MOV_LAUNCHED=1, then waits for a keypress
    before closing.
    """
    app_name = os.environ.get("COMPRESS_MOV_TERMINAL", "Terminal")

    # The new terminal starts in $HOME, so relative paths must be made
    # absolute here. Finder already passes absolute paths; this covers
    # manual runs with relative ones.
    abs_args = [a if a.startswith("-") else str(Path(a).resolve()) for a in argv]

    tmp_dir = Path(tempfile.mkdtemp(prefix="compress-mov.", dir=os.environ.get("TMPDIR", "/tmp")))
    launcher = tmp_dir / "compress-mov.command"

    script = build_launcher_script(command or self_command(), abs_args, tmp_dir)
    launcher.write_text(script)
    launcher.chmod(0o755)

    try:
        subprocess.run([OPEN, "-a", app_name, str(launcher)], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print(f"compress-mov: could not open {app_name}", file=sys.stderr)
        sys.exit(1)
