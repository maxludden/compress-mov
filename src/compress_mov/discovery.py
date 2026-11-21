"""Resolving CLI arguments (files and directories) to a de-duplicated list
of .mov files to encode.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from .ui import console

# Where scan notes go. The CLI prints them to the console; other front ends
# (the TUI) pass their own sink so nothing is written over their screen.
Say = Callable[[str], None]


def _is_wanted(path: Path, directory: Path) -> bool:
    if path.suffix.lower() != ".mov":
        return False
    if any(part.startswith(".") for part in path.relative_to(directory).parts):
        return False
    try:
        return path.is_file()
    except OSError:  # e.g. permission denied while stat-ing
        return False


def find_mov_files(directory: Path, recursive: bool, say: Say = console.print) -> list[Path]:
    """.mov files directly inside `directory`, or at every depth with `recursive`.

    Matching is case-insensitive (.mov/.MOV/.Mov/...). Hidden files and
    anything under a hidden directory are skipped: that covers the ``._*``
    AppleDouble sidecars macOS writes on exFAT/SMB volumes (not real
    videos), and ``.Trashes``/``.Spotlight-V100`` and friends. A directory
    that can't be read yields nothing (with a note) instead of aborting.
    """
    try:
        candidates = list(directory.rglob("*") if recursive else directory.iterdir())
    except OSError as exc:
        say(f"  skipping (can't read {directory.name}/): {exc.strerror or exc}")
        return []
    return sorted(p for p in candidates if _is_wanted(p, directory))


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def rejoin_split_paths(paths: list[Path], say: Say = console.print) -> list[Path]:
    """Repair a path with unquoted spaces that the shell split into pieces.

    ``compress-mov ~/Library/Mobile Documents/x.MOV`` reaches us as two
    arguments. When an argument doesn't exist, try gluing it to the ones
    after it with single spaces (shortest join first); if that names a real
    path, use it in place of the pieces. Arguments that exist are never
    touched, and anything that can't be repaired is passed through so it is
    reported as "not found" as usual.
    """
    fixed: list[Path] = []
    i = 0
    while i < len(paths):
        if _exists(paths[i]):
            fixed.append(paths[i])
            i += 1
            continue
        joined = str(paths[i])
        for j in range(i + 1, len(paths)):
            joined += f" {paths[j]}"
            candidate = Path(joined).expanduser()
            if _exists(candidate):
                say(f"  unquoted spaces in path, using: {candidate}")
                fixed.append(candidate)
                i = j + 1
                break
        else:
            fixed.append(paths[i])
            i += 1
    return fixed


def resolve_inputs(paths: list[Path], recursive: bool, say: Say = console.print) -> list[Path]:
    """Expand files/directories from the CLI into a flat, de-duplicated list.

    Each directory is expanded to the .mov files inside it. A file is used
    as-is if it has a .mov extension. Anything else is skipped with a note
    reported through `say` (the console by default). A folder plus a file
    already inside it, passed together, is only encoded once. A path split by
    unquoted spaces is rejoined first (see `rejoin_split_paths`).
    """
    paths = rejoin_split_paths(paths, say)
    say(f"[bold]▸[/bold] Scanning {len(paths)} input(s)…")

    videos: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        try:
            is_dir, is_file = raw.is_dir(), raw.is_file()
        except OSError as exc:
            say(f"  skipping (can't access {raw}): {exc.strerror or exc}")
            continue
        if is_dir:
            found = find_mov_files(raw, recursive, say)
            say(f"  {raw.name}/: {len(found)} .mov file(s)")
        elif is_file:
            if raw.suffix.lower() == ".mov":
                found = [raw]
            else:
                say(f"  skipping (not a .mov): {raw.name}")
                found = []
        else:
            say(f"  skipping (not found): {raw}")
            found = []

        for f in found:
            # De-duplicate on the real file, but keep the path as given so the
            # output lands next to a symlink, not next to what it points at.
            real = f.resolve()
            if real not in seen:
                seen.add(real)
                videos.append(Path(os.path.abspath(f)))

    return videos
