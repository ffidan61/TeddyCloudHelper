"""Jinja2 rendering: template → file, with a timestamped ``.bak`` first.

Every config change in the tool follows the same cycle (see CLAUDE.md):
update state → re-render templates → prompt for restart. This module is the
middle step; it never touches state and never talks to Docker.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined

_env = Environment(
    loader=PackageLoader("teddycloudhelper", "templates"),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    autoescape=False,
)


def render_template(template_name: str, context: dict) -> str:
    return _env.get_template(template_name).render(context)


# Timestamped .baks accumulate on every re-render (each settings change
# writes one) — keep enough history for any realistic rollback, drop the rest.
KEEP_BACKUPS = 10


def render_to_file(template_name: str, dest: Path, context: dict) -> Path:
    """Render *template_name* to *dest*, backing up an existing file first."""
    text = render_template(template_name, context)
    if dest.is_file():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(dest, dest.with_name(f"{dest.name}.{stamp}.bak"))
        _prune_backups(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8", newline="\n")
    return dest


def _prune_backups(dest: Path) -> None:
    # The timestamp format sorts lexicographically, so sorted() is oldest
    # first; the newest KEEP_BACKUPS survive.
    backups = sorted(dest.parent.glob(f"{dest.name}.*.bak"))
    for old in backups[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
