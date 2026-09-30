from __future__ import annotations

from pathlib import Path

from compress_mov.discovery import find_mov_files, resolve_inputs


def test_find_mov_files_is_case_insensitive_and_non_recursive(tmp_path: Path) -> None:
    (tmp_path / "a.mov").touch()
    (tmp_path / "b.MOV").touch()
    (tmp_path / "c.Mov").touch()
    (tmp_path / "not-a-video.txt").touch()
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.mov").touch()

    found = find_mov_files(tmp_path, recursive=False)

    assert {p.name for p in found} == {"a.mov", "b.MOV", "c.Mov"}


def test_find_mov_files_recursive_includes_nested(tmp_path: Path) -> None:
    (tmp_path / "a.mov").touch()
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.mov").touch()

    found = find_mov_files(tmp_path, recursive=True)

    assert {p.name for p in found} == {"a.mov", "nested.mov"}


def test_resolve_inputs_expands_directory(tmp_path: Path) -> None:
    (tmp_path / "a.mov").touch()
    (tmp_path / "b.mov").touch()

    videos = resolve_inputs([tmp_path], recursive=False)

    assert sorted(p.name for p in videos) == ["a.mov", "b.mov"]


def test_resolve_inputs_accepts_a_bare_mov_file(tmp_path: Path) -> None:
    f = tmp_path / "clip.mov"
    f.touch()

    videos = resolve_inputs([f], recursive=False)

    assert videos == [f.resolve()]


def test_resolve_inputs_skips_non_mov_file(tmp_path: Path) -> None:
    f = tmp_path / "notes.txt"
    f.touch()

    videos = resolve_inputs([f], recursive=False)

    assert videos == []


def test_resolve_inputs_skips_missing_path(tmp_path: Path) -> None:
    videos = resolve_inputs([tmp_path / "ghost.mov"], recursive=False)

    assert videos == []


def test_resolve_inputs_dedupes_directory_and_file_inside_it(tmp_path: Path) -> None:
    f = tmp_path / "clip.mov"
    f.touch()

    videos = resolve_inputs([tmp_path, f], recursive=False)

    assert videos == [f.resolve()]


def test_resolve_inputs_preserves_first_seen_order(tmp_path: Path) -> None:
    a = tmp_path / "a.mov"
    b = tmp_path / "b.mov"
    a.touch()
    b.touch()

    videos = resolve_inputs([b, a], recursive=False)

    assert videos == [b.resolve(), a.resolve()]


def test_find_mov_files_skips_appledouble_sidecars(tmp_path: Path) -> None:
    (tmp_path / "IMG_1.mov").touch()
    (tmp_path / "._IMG_1.mov").touch()
    (tmp_path / ".hidden.mov").touch()

    found = find_mov_files(tmp_path, recursive=False)

    assert [p.name for p in found] == ["IMG_1.mov"]


def test_find_mov_files_recursive_skips_hidden_directories(tmp_path: Path) -> None:
    (tmp_path / "a.mov").touch()
    trash = tmp_path / ".Trashes"
    trash.mkdir()
    (trash / "deleted.mov").touch()
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "._nested.mov").touch()
    (sub / "nested.mov").touch()

    found = find_mov_files(tmp_path, recursive=True)

    assert {p.name for p in found} == {"a.mov", "nested.mov"}


def test_find_mov_files_works_when_directory_itself_is_hidden(tmp_path: Path) -> None:
    hidden = tmp_path / ".footage"
    hidden.mkdir()
    (hidden / "clip.mov").touch()

    found = find_mov_files(hidden, recursive=True)

    assert [p.name for p in found] == ["clip.mov"]
