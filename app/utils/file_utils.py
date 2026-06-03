"""File utilities used across services.

These helpers harden the dossier walk against the cases the project's
privacy and robustness guarantees depend on:

* **Symlink containment.** ``Path.is_file()`` follows symlinks, so a
  symlink dropped inside the chosen dossier folder (e.g.
  ``evidence.txt -> ~/.ssh/id_rsa``) would otherwise be read and its
  content shipped to the LLM, breaking "original files never leave the
  machine". :func:`iter_dossier_files` skips symlinked entries and any
  real path that resolves outside the chosen folder.
* **Size / count caps.** A single huge file or an enormous folder would
  otherwise be read fully into memory and hang the app. The walk is
  bounded by :data:`MAX_DOSSIER_FILES` and individual oversized files are
  reported via :func:`is_within_size_limit` so callers can degrade
  gracefully instead of reading them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from app.config import SUPPORTED_DOSSIER_EXTENSIONS


# Hard caps for the dossier walk. These bound a self-inflicted memory/time
# blow-up on a single-user local tool; they are deliberately generous.
MAX_DOSSIER_FILES = 2000
MAX_DOSSIER_FILE_BYTES = 25 * 1024 * 1024  # 25 MB per file


def is_within_folder(path: Path, folder: Path) -> bool:
    """Return True if ``path`` resolves to a location inside ``folder``.

    Both paths are fully resolved (following symlinks) before comparison,
    so a symlink whose target escapes ``folder`` is rejected.
    """
    try:
        real = path.resolve()
        folder_real = folder.resolve()
    except OSError:
        return False
    if real == folder_real:
        return True
    return folder_real in real.parents


def is_within_size_limit(path: Path, max_bytes: int = MAX_DOSSIER_FILE_BYTES) -> bool:
    """Return True if ``path`` is at or below ``max_bytes``.

    Returns False for files whose size cannot be determined so an
    unreadable/odd entry is treated as "skip", not "read unbounded".
    """
    try:
        return path.stat().st_size <= max_bytes
    except OSError:
        return False


def iter_contained_files(
    folder: str | Path,
    *,
    max_files: int = MAX_DOSSIER_FILES,
) -> Iterator[Path]:
    """Yield regular files under ``folder`` that are safe to consider.

    This is the security primitive the dossier walk is built on:

    * **Symlinks are skipped** — both symlinked files and any entry whose
      resolved real path falls outside ``folder`` (containment check), so
      content from elsewhere on disk can never enter the dossier.
    * **The walk is bounded** by ``max_files`` so an enormous folder cannot
      hang the app; callers are responsible for surfacing truncation.

    No extension filtering is applied here, so callers that need to report
    unsupported files (e.g. the folder validator) still see them.

    ``rglob`` does not descend into symlinked *directories* by default, so
    a directory-symlink loop is not reachable through this walk.
    """
    folder = Path(folder).expanduser()
    if not folder.exists() or not folder.is_dir():
        return

    yielded = 0
    for path in sorted(folder.rglob("*")):
        if yielded >= max_files:
            return
        # Skip symlinks outright (both files and dirs) and anything that
        # resolves outside the chosen folder.
        if path.is_symlink():
            continue
        if not path.is_file():
            continue
        if not is_within_folder(path, folder):
            continue
        yielded += 1
        yield path


def iter_dossier_files(
    folder: str | Path,
    *,
    max_files: int = MAX_DOSSIER_FILES,
) -> Iterator[Path]:
    """Yield contained, supported-extension dossier files under ``folder``."""
    for path in iter_contained_files(folder, max_files=max_files):
        if path.suffix.lower() in SUPPORTED_DOSSIER_EXTENSIONS:
            yield path


def iter_supported_files(folder: str | Path):
    """Backwards-compatible alias for :func:`iter_dossier_files`.

    Retained so existing imports keep working; new code should call
    :func:`iter_dossier_files` directly.
    """
    yield from iter_dossier_files(folder)
