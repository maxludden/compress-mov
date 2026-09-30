"""Shared console for user-facing output.

Everything is written to stderr, matching the original shell scripts'
``>&2`` convention -- stdout stays free for piping/scripting.
"""

from rich.console import Console

console = Console(stderr=True)
