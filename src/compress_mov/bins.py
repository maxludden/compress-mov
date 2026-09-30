"""Absolute paths to external binaries.

A Finder Quick Action runs with a minimal ``$PATH`` that doesn't include
Homebrew, so every external tool is invoked by absolute path rather than
relying on lookup.
"""

FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"
OPEN = "/usr/bin/open"
OSASCRIPT = "/usr/bin/osascript"
CAFFEINATE = "/usr/bin/caffeinate"
