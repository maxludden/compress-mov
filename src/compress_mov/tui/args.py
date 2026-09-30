"""Argument parsing for ``compress-mov-tui`` (no Textual import)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class TuiArgs:
    paths: list[Path] = field(default_factory=list)
    recursive: bool = False
    keep_larger: bool = False


def parse_args(argv: Sequence[str] | None = None) -> TuiArgs:
    parser = argparse.ArgumentParser(
        prog="compress-mov-tui",
        description="Terminal UI for compress-mov: queue .mov files or folders and compress them to HEVC .mp4.",
    )
    parser.add_argument("paths", nargs="*", type=Path, metavar="FILE|DIR", help="files or folders to queue on startup")
    parser.add_argument("-r", "--recursive", action="store_true", help="include .mov files in subdirectories")
    parser.add_argument("--keep-larger", action="store_true", help="keep the .mp4 even when it isn't smaller")
    ns = parser.parse_args(argv)
    return TuiArgs(paths=list(ns.paths), recursive=ns.recursive, keep_larger=ns.keep_larger)
