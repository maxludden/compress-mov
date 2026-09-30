"""A Rich-free stand-in for ``rich.progress.Progress`` (no Textual import).

The encoder reports through a small interface (see
:class:`compress_mov.encode.ProgressLike`); this adapter implements it by
forwarding to callbacks, so a front end can show progress its own way.
Callbacks run on the encode thread: front ends must marshal to their UI thread.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from rich.progress import TaskID

# (description, total seconds or None if unknown, completed seconds)
OnProgress = Callable[[str, float | None, float], None]
# (message, style or None)
OnNote = Callable[[str, str | None], None]

MIN_UPDATE_INTERVAL = 0.1  # ffmpeg emits a burst of lines per tick; don't flood the UI


@dataclass
class _Task:
    description: str
    total: float | None
    completed: float = 0.0


class _NoteConsole:
    def __init__(self, on_note: OnNote) -> None:
        self._on_note = on_note

    def print(self, message: str, *, style: str | None = None) -> None:
        self._on_note(message, style)


class ProgressAdapter:
    def __init__(self, on_progress: OnProgress, on_note: OnNote) -> None:
        self._on_progress = on_progress
        self._console = _NoteConsole(on_note)
        self._tasks: dict[TaskID, _Task] = {}
        self._next_id = 0
        self._last_emit = 0.0

    @property
    def console(self) -> _NoteConsole:
        return self._console

    def add_task(self, description: str, *, total: float | None = None) -> TaskID:
        task_id = TaskID(self._next_id)
        self._next_id += 1
        self._tasks[task_id] = _Task(description, total)
        self._emit(task_id, force=True)
        return task_id

    def update(self, task_id: TaskID, *, completed: float | None = None, description: str | None = None) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        if completed is not None:
            task.completed = completed
        if description is not None:
            task.description = description
        self._emit(task_id)

    def remove_task(self, task_id: TaskID) -> None:
        self._tasks.pop(task_id, None)

    def _emit(self, task_id: TaskID, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_emit < MIN_UPDATE_INTERVAL:
            return
        self._last_emit = now
        task = self._tasks[task_id]
        self._on_progress(task.description, task.total, task.completed)
