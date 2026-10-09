"""Archive Station: persistent downloads for Internet Archive items."""

from pathlib import Path

__version__ = "1.0.0"

try:
    __package_version__ = Path(__file__).with_name("VERSION").read_text().strip()
except FileNotFoundError:
    __package_version__ = __version__
