"""TeddyCloudHelper — interactive CLI toolkit to set up and manage a TeddyCloud server."""

from importlib.metadata import PackageNotFoundError, version

# Single source of truth is pyproject.toml — a hardcoded string here drifted
# out of sync with releases, making the tool nag about "updates" to itself.
try:
    __version__ = version("teddycloudhelper")
except PackageNotFoundError:  # pragma: no cover - running without install
    __version__ = "0+unknown"
