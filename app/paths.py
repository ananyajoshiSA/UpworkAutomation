"""Single source of truth for every path the app resolves.

This is the ONE helper used by ``app.config``, ``desktop_app.py``, and the
launcher to decide:

* where read-only bundled resources live (``app/``, ``.streamlit/``,
  ``.env.example``) — :func:`resource_base` / :func:`resource_path`, and
* where user-writable state lives (the API-key ``.env`` and rotating logs) —
  :func:`state_dir`.

It is deliberately dependency-free (stdlib only) so it can be imported from
anywhere — including the launcher, before anything else is set up — with no
risk of an import cycle.

Three run contexts must all work, and this module is what makes them
indistinguishable to the rest of the app:

1. **Dev checkout** — ``streamlit run app/main.py`` (or pytest). State stays in
   the repo root; behaviour is unchanged.
2. **Zero-touch desktop app** — the embeddable-Python launcher sets
   ``UPS_PACKAGED=1`` and runs ``desktop_app.py`` from the project folder.
   State moves to ``%APPDATA%`` so the (possibly relocated, possibly
   read-only) folder is never written to.
3. **PyInstaller bundle** — ``sys.frozen`` is set and resources live under
   ``sys._MEIPASS``; state again moves to ``%APPDATA%``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


# Brand folder name used under %APPDATA% (and mirrored by the Windows
# installer + the launcher's ensure_runtime step). Keep these in sync.
APP_DIRNAME = "UpworkProposalStrategist"

# Environment flag the desktop launcher sets to declare "this is the packaged
# desktop app, use per-user state" — even though an embeddable-Python run is
# not technically ``sys.frozen``. Dev ``streamlit run`` leaves it unset.
PACKAGED_ENV_FLAG = "UPS_PACKAGED"


def is_frozen() -> bool:
    """True when running from a PyInstaller one-file/one-dir bundle."""
    return bool(getattr(sys, "frozen", False))


def is_packaged() -> bool:
    """True when running as the end-user desktop app (any packaged form).

    Covers both packaged forms — a PyInstaller bundle (``sys.frozen``) and the
    embeddable-Python launcher (which exports ``UPS_PACKAGED=1``) — so writable
    state is redirected off the install folder in both. A plain dev / pytest /
    ``streamlit run`` invocation returns False and keeps state in the repo root.
    """
    return is_frozen() or os.environ.get(PACKAGED_ENV_FLAG, "") == "1"


def resource_base() -> Path:
    """Directory that holds read-only bundled resources.

    * PyInstaller bundle → ``sys._MEIPASS`` (the unpacked bundle dir).
    * Everything else → the repo root (the parent of the ``app`` package).

    Resolved from this file's location, so it is correct even after the whole
    project folder is moved or renamed (the embeddable-runtime relocation test).
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    # this file is <root>/app/paths.py → parent.parent is <root>.
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    """Absolute path to a bundled resource, e.g. ``resource_path("app", "main.py")``."""
    return resource_base().joinpath(*parts)


def user_state_dir() -> Path:
    """Per-user writable directory for app state, OUTSIDE the install folder.

    On Windows this is ``%APPDATA%\\UpworkProposalStrategist`` — stable across
    app restarts AND across moving/reinstalling the program folder. The
    non-Windows branch keeps a frozen build sane if one is ever produced
    elsewhere (the supported packaged target is Windows).
    """
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(base) / APP_DIRNAME
    return Path(os.path.expanduser("~")) / ".upwork_proposal_strategist"


def state_dir() -> Path:
    """Where the app reads/writes ``.env`` and logs.

    Per-user location when packaged; the repo root in a dev checkout (so tests
    and ``streamlit run`` are unaffected). The directory is created lazily by
    the caller (see :mod:`app.config`).
    """
    return user_state_dir() if is_packaged() else resource_base()
