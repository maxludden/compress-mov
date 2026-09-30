"""Relaunching in a real terminal for Finder Quick Action use.

A Quick Action has no terminal, so when stderr isn't a TTY the CLI
reopens itself in Terminal.app (or $COMPRESS_MOV_TERMINAL, e.g. iTerm)
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


def relaunch_in_terminal(argv: list[str], self_path: Path) -> None:
    """Reopen `self_path` with `argv` inside a Terminal.app window.

    Writes a `.command` launcher to a scratch dir (sidesteps AppleScript
    string escaping entirely) and hands it to `open -a <app>`. The
    launcher deletes its own directory, runs the script with
    COMPRESS_MOV_LAUNCHED=1, then waits for a keypress before closing.
    """
    app_name = os.environ.get("COMPRESS_MOV_TERMINAL", "Terminal")

    # The new terminal starts in $HOME, so relative paths must be made
    # absolute here. Finder already passes absolute paths; this covers
    # manual runs with relative ones.
    abs_args = [a if a.startswith("-") else str(Path(a).resolve()) for a in argv]

    tmp_dir = Path(tempfile.mkdtemp(prefix="compress-mov.", dir=os.environ.get("TMPDIR", "/tmp")))
    launcher = tmp_dir / "compress-mov.command"

    quoted_args = " ".join(shlex.quote(a) for a in abs_args)
    script = textwrap.dedent(f"""\
        #!/bin/zsh
        rm -rf -- {shlex.quote(str(tmp_dir))}
        COMPRESS_MOV_LAUNCHED=1 {shlex.quote(str(self_path))} {quoted_args}
        print
        read -k1 "?Done — press any key to close…"
        exit
        """)
    launcher.write_text(script)
    launcher.chmod(0o755)

    try:
        subprocess.run([OPEN, "-a", app_name, str(launcher)], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print(f"compress-mov: could not open {app_name}", file=sys.stderr)
        sys.exit(1)
