"""Owner-only files for key material (private keys, .p12 bundles, backups).

Files are created with mode 0600 from the start — writing first and
chmod-ing afterwards leaves a window in which the file is world-readable.
A pre-existing file keeps its old mode on ``os.open``, hence the extra
chmod. Both are best effort and meaningless on Windows.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

PRIVATE_MODE = 0o600


def write_private(path: Path, data: bytes) -> None:
    """Write *data* to *path*, readable by the owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, PRIVATE_MODE)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    with contextlib.suppress(OSError):
        path.chmod(PRIVATE_MODE)
