"""compress-mov — HEVC .mp4 encoder with a Finder Quick Action front end."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("compress-mov")
except PackageNotFoundError:  # running from a source checkout, not installed
    __version__ = "0.0.0"
