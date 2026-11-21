from __future__ import annotations

from pathlib import Path

from compress_mov.discovery import find_mov_files, rejoin_split_paths, resolve_inputs


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


def test_resolve_inputs_keeps_symlink_path_so_output_lands_beside_the_link(tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    target = media / "real.mov"
    target.write_bytes(b"x")
    project = tmp_path / "project"
    project.mkdir()
    link = project / "clip.mov"
    link.symlink_to(target)

    videos = resolve_inputs([project], recursive=False)

    assert videos == [link]
    assert videos[0].parent == project  # not media/


def test_resolve_inputs_dedupes_a_symlink_and_its_target(tmp_path: Path) -> None:
    target = tmp_path / "real.mov"
    target.write_bytes(b"x")
    link = tmp_path / "alias.mov"
    link.symlink_to(target)

    videos = resolve_inputs([link, target], recursive=False)

    assert videos == [link]


def test_resolve_inputs_normalises_dotdot_without_following_symlinks(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "sub").mkdir()
    f = tmp_path / "clip.mov"
    f.write_bytes(b"x")
    monkeypatch.chdir(tmp_path / "sub")

    videos = resolve_inputs([Path("../clip.mov")], recursive=False)

    assert videos == [f]


def test_broken_symlink_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "dangling.mov").symlink_to(tmp_path / "gone.mov")

    assert find_mov_files(tmp_path, recursive=False) == []


def test_unreadable_directory_is_skipped_not_fatal(tmp_path: Path, monkeypatch) -> None:
    good = tmp_path / "good"
    good.mkdir()
    (good / "a.mov").touch()
    locked = tmp_path / "locked"
    locked.mkdir()
    real_iterdir = Path.iterdir

    def iterdir(self: Path):
        if self == locked:
            raise PermissionError(13, "Permission denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)

    videos = resolve_inputs([locked, good], recursive=False)

    assert [p.name for p in videos] == ["a.mov"]


def test_scan_notes_go_to_the_given_sink_not_the_console(tmp_path: Path, capsys) -> None:
    (tmp_path / "a.mov").touch()
    notes: list[str] = []

    videos = resolve_inputs([tmp_path, tmp_path / "ghost.mov"], recursive=False, say=notes.append)

    assert [p.name for p in videos] == ["a.mov"]
    assert any("Scanning 2 input(s)" in n for n in notes)
    assert any("skipping (not found)" in n for n in notes)
    assert capsys.readouterr().err == ""


def test_unreadable_directory_note_uses_the_sink(tmp_path: Path, monkeypatch) -> None:
    def boom(self: Path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "iterdir", boom)
    notes: list[str] = []

    assert find_mov_files(tmp_path, recursive=False, say=notes.append) == []
    assert notes and "can't read" in notes[0]


def test_resolve_inputs_rejoins_path_split_on_unquoted_spaces(tmp_path: Path) -> None:
    folder = tmp_path / "Mobile Documents" / "my clips"
    folder.mkdir(parents=True)
    clip = folder / "a.MOV"
    clip.write_bytes(b"x")
    pieces = [Path(p) for p in str(clip).split(" ")]
    notes: list[str] = []
    assert resolve_inputs(pieces, recursive=False, say=notes.append) == [clip]
    assert any("unquoted spaces" in n for n in notes)
    assert not any("not found" in n for n in notes)


def test_rejoin_leaves_existing_and_unfixable_paths_alone(tmp_path: Path) -> None:
    real = tmp_path / "a.mov"
    real.write_bytes(b"x")
    missing = [tmp_path / "nope", tmp_path / "still nope"]
    assert rejoin_split_paths([real, *missing], say=lambda _m: None) == [real, *missing]


def _quiet(_m: str) -> None:
    pass


def test_rejoin_never_swallows_an_argument_that_exists_on_its_own(tmp_path: Path, monkeypatch) -> None:
    # "gone" is missing, "keep.mov" is a real input, and "gone keep.mov" happens to exist too.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "keep.mov").write_bytes(b"x")
    (tmp_path / "gone keep.mov").write_bytes(b"x")
    args = [tmp_path / "gone", Path("keep.mov")]
    assert rejoin_split_paths(args, say=_quiet) == args


def test_rejoin_prefers_the_longest_existing_join_over_a_directory_prefix(tmp_path: Path) -> None:
    # "/t/my dir/my" is an existing folder, but the intended path is the file under "my sub".
    (tmp_path / "my dir" / "my").mkdir(parents=True)
    clip = tmp_path / "my dir" / "my sub" / "clip.mov"
    clip.parent.mkdir()
    clip.write_bytes(b"x")
    pieces = [Path(p) for p in str(clip).split(" ")]
    assert rejoin_split_paths(pieces, say=_quiet) == [clip]


def test_rejoin_can_start_from_a_first_piece_that_exists(tmp_path: Path) -> None:
    (tmp_path / "My").mkdir()
    clip = tmp_path / "My Clips" / "a.mov"
    clip.parent.mkdir()
    clip.write_bytes(b"x")
    pieces = [tmp_path / "My", Path("Clips/a.mov")]
    assert rejoin_split_paths(pieces, say=_quiet) == [clip]


def test_rejoin_does_not_expand_or_choke_on_tilde(tmp_path: Path) -> None:
    pieces = [Path("~no_such_user_xyz/a"), Path("b.mov")]
    assert rejoin_split_paths(pieces, say=_quiet) == pieces
