"""Resolving CLI arguments (files and directories) to a de-duplicated list
of .mov files to encode.
"""

from __future__ import annotations

from pathlib import Path

from .ui import console


def find_mov_files(directory: Path, recursive: bool) -> list[Path]:
    """.mov files directly inside `directory`, or at every depth with `recursive`.

    Matching is case-insensitive (.mov/.MOV/.Mov/...). Hidden files and
    anything under a hidden directory are skipped: that covers the ``._*``
    AppleDouble sidecars macOS writes on exFAT/SMB volumes (not real
    videos), and ``.Trashes``/``.Spotlight-V100`` and friends.
    """
    candidates = directory.rglob("*") if recursive else directory.iterdir()
    return sorted(
        p
        for p in candidates
        if p.suffix.lower() == ".mov"
        and not any(part.startswith(".") for part in p.relative_to(directory).parts)
        and p.is_file()
    )


def resolve_inputs(paths: list[Path], recursive: bool) -> list[Path]:
    """Expand files/directories from the CLI into a flat, de-duplicated list.

    Each directory is expanded to the .mov files inside it. A file is used
    as-is if it has a .mov extension. Anything else is skipped with a note
    printed to the console. A folder plus a file already inside it, passed
    together, is only encoded once.
    """
    console.print(f"[bold]▸[/bold] Scanning {len(paths)} input(s)…")

    videos: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        if raw.is_dir():
            found = find_mov_files(raw, recursive)
            console.print(f"  {raw.name}/: {len(found)} .mov file(s)")
        elif raw.is_file():
            if raw.suffix.lower() == ".mov":
                found = [raw]
            else:
                console.print(f"  skipping (not a .mov): {raw.name}")
                found = []
        else:
            console.print(f"  skipping (not found): {raw}")
            found = []

        for f in found:
            real = f.resolve()
            if real not in seen:
                seen.add(real)
                videos.append(real)

    return videos
