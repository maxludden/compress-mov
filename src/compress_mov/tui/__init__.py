"""Optional Textual front end for compress-mov (``compress-mov-tui``).

Everything here is opt-in and isolated from the CLI: the ``compress-mov``
command never imports this package, and this package only imports Textual
(an optional extra) once arguments have parsed, so ``--help`` and the
"not installed" message work without it.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from .args import parse_args

INSTALL_HINT = (
    "compress-mov-tui needs the optional TUI dependencies.\n"
    "Install them with:  uv tool install 'compress-mov[tui]'   (or: pip install 'compress-mov[tui]')"
)


def run(argv: Sequence[str] | None = None) -> None:
    """Entry point for the ``compress-mov-tui`` script."""
    args = parse_args(argv)

    try:
        from .app import CompressApp
    except ModuleNotFoundError as exc:
        if (exc.name or "").partition(".")[0] != "textual":
            raise  # a real bug, not a missing extra
        print(INSTALL_HINT, file=sys.stderr)
        raise SystemExit(1) from exc

    app = CompressApp(args)
    app.run()
    raise SystemExit(app.return_code or 0)
