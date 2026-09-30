from __future__ import annotations

import os

import pytest

from compress_mov import power


class _Proc:
    def __init__(self) -> None:
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True


def test_caffeinate_holds_assertion_tied_to_our_pid_then_releases(monkeypatch: pytest.MonkeyPatch) -> None:
    proc, seen = _Proc(), []
    monkeypatch.setattr(power.subprocess, "Popen", lambda cmd, **kw: seen.append(cmd) or proc)

    with power.caffeinate():
        assert not proc.terminated

    assert seen == [[power.CAFFEINATE, "-i", "-w", str(os.getpid())]]
    assert proc.terminated


def test_caffeinate_releases_even_if_the_body_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _Proc()
    monkeypatch.setattr(power.subprocess, "Popen", lambda cmd, **kw: proc)

    with pytest.raises(RuntimeError), power.caffeinate():
        raise RuntimeError

    assert proc.terminated


def test_caffeinate_is_a_no_op_when_the_binary_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(power.subprocess, "Popen", boom)

    with power.caffeinate():
        pass
