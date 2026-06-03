"""Browser launcher for the Upwork Proposal Strategist.

What this does
--------------
The app is a local Streamlit server. This launcher starts that server and opens
it in the user's **default web browser** — there is no native window and no
extra GUI runtime (no pywebview / WebView2 / .NET). It runs in the **foreground**,
so the console window it launches from is the "app is running" indicator:
closing that window stops the server cleanly.

Flow
----
1. Declare packaged-desktop mode (``UPS_PACKAGED=1``) so writable state — the
   API-key ``.env`` and rotating logs — goes to ``%APPDATA%`` instead of the
   (possibly read-only / relocatable) install folder. See :mod:`app.paths`.
2. Create the per-user ``.env`` from the bundled template on first run.
3. Pick a FREE local port (never hard-code 8501, so two copies don't collide).
4. On a background thread, wait for the server's health endpoint and then open
   the browser at the local URL — opening only once it actually answers avoids a
   "can't connect" tab on the slow first launch.
5. Run Streamlit **headless**, bound to 127.0.0.1, in the foreground. It serves
   until the console window is closed (or Ctrl+C), which cleanly stops it.

Design notes
------------
* ONE process. The launcher *is* the Streamlit server: its stable CLI is driven
  in-process by :func:`run_streamlit_cli`. There is no child process, GUI library,
  or re-exec, so closing the console is the clean, orphan-free shutdown — nothing
  to taskkill.
* 127.0.0.1 is a browser "secure context", so clipboard paste on the Job
  Screenshot screen keeps working; the file-upload control is always available
  too.
* Writable state is handled by :mod:`app.config`, which redirects to a per-user
  location when packaged — see DEVELOPER.md.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

# The one shared path helper (stdlib-only, no import cycle). `app` is always
# importable here: its directory (the repo root) is on sys.path[0] as a script,
# and on sys.path inside the embeddable-Python runtime once it runs from the
# project folder.
from app.paths import PACKAGED_ENV_FLAG, resource_base


# Mirrors app.config.APP_TITLE. Kept as a literal so the launcher can print the
# banner without importing the (heavier) app package first.
APP_TITLE = "Upwork Proposal Strategist"

# How long to wait for the Streamlit server to answer before opening the browser
# anyway. The first run imports a lot (and antivirus can slow it), so this is
# deliberately generous.
BOOT_TIMEOUT = 90.0


def _main_script() -> Path:
    """Absolute path to the Streamlit entry script Streamlit will execute."""
    return resource_base() / "app" / "main.py"


# ---------------------------------------------------------------------------
# Streamlit server (driven in-process via its own stable CLI)
# ---------------------------------------------------------------------------


def run_streamlit_cli(port: int) -> None:
    """Run the Streamlit server (headless) on ``port``. Blocks until stopped.

    Points Streamlit at the bundled ``app/main.py`` and pins every server option
    on the command line so behavior never depends on a config file being found.
    Working directory is the resource base so ``import app...`` resolves and the
    bundled ``.streamlit/config.toml`` (theme, toolbar) is still picked up.
    """
    base = resource_base()
    # CWD so Streamlit finds .streamlit/config.toml; sys.path so `import app`
    # works when Streamlit execs app/main.py.
    os.chdir(str(base))
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))

    # Quiet console on a normal run; full logs when launched via the debug .bat.
    log_level = "debug" if os.environ.get("UPS_DEBUG") == "1" else "error"
    sys.argv = [
        "streamlit",
        "run",
        str(_main_script()),
        "--server.address=127.0.0.1",
        f"--server.port={port}",
        "--server.headless=true",            # we open the browser ourselves
        "--browser.gatherUsageStats=false",  # local-first: no telemetry call
        "--server.fileWatcherType=none",     # packaged app: nothing to hot-reload
        "--server.runOnSave=false",
        # Local single-user desktop context: these clear the 403 upload error
        # local proxies otherwise trigger on st.file_uploader. Mirrors the
        # bundled .streamlit/config.toml.
        "--server.enableCORS=false",
        "--server.enableXsrfProtection=false",
        "--server.maxUploadSize=20",
        f"--logger.level={log_level}",
    ]
    # Imported here (not at module top) so the cost is only paid in the process
    # that actually serves, and import errors surface in the console.
    from streamlit.web import cli as stcli

    sys.exit(stcli.main())


# ---------------------------------------------------------------------------
# Port / health / browser
# ---------------------------------------------------------------------------


def pick_free_port() -> int:
    """Return an OS-assigned free TCP port on the loopback interface.

    Binding to port 0 lets the kernel hand us a port nothing else holds, so a
    second instance never collides with the first. The socket is closed
    immediately; Streamlit re-binds the same port a moment later.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _health_ok(port: int) -> bool:
    """True if the Streamlit server answers its health endpoint with HTTP 200."""
    # /_stcore/health is current; /healthz covers older Streamlit just in case.
    for path in ("/_stcore/health", "/healthz"):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}{path}", timeout=2
            ) as resp:
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - not up yet / endpoint absent → try next
            continue
    return False


def _open_browser_when_ready(
    port: int, url: str, timeout: float = BOOT_TIMEOUT
) -> None:
    """Wait for the server to be healthy, then open the default browser once.

    Runs on a daemon thread so the main thread is free to serve. If the server
    never comes up within ``timeout`` we still open the browser once, so the
    user sees the address (and any browser-side error) rather than nothing.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _health_ok(port):
            break
        time.sleep(0.4)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - never crash the server if no browser opens
        pass


# ---------------------------------------------------------------------------
# First-run setup
# ---------------------------------------------------------------------------


def _bootstrap_env() -> None:
    """Create the per-user ``.env`` from the bundled template on first run.

    The write target comes from :data:`app.config.ENV_PATH`, which already
    redirects to ``%APPDATA%\\UpworkProposalStrategist`` when packaged, so the
    Setup screen and this bootstrap always agree on one location.
    """
    try:
        from app.config import ENV_PATH  # packaged-aware target
    except Exception:  # noqa: BLE001 - never block launch on config import
        return
    if ENV_PATH.exists():
        return
    try:
        ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
        template = resource_base() / ".env.example"
        if template.is_file():
            shutil.copyfile(template, ENV_PATH)
        else:
            ENV_PATH.write_text("LLM_PROVIDER=openai\n", encoding="utf-8")
    except OSError:  # pragma: no cover - best effort; Setup can still write it
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    # Declare packaged-desktop mode (unless explicitly overridden) so app.config
    # redirects writable state — the API-key .env and logs — to %APPDATA%
    # instead of the read-only / relocatable install folder. A developer can
    # force dev behaviour with UPS_PACKAGED=0.
    os.environ.setdefault(PACKAGED_ENV_FLAG, "1")

    _bootstrap_env()
    port = pick_free_port()
    url = f"http://127.0.0.1:{port}"

    # Print the address up front (with logger.level=error the console stays
    # readable) so the user can open it manually if their browser doesn't.
    print(
        f"\n{APP_TITLE} is starting…\n"
        f"If your browser does not open automatically, go to:  {url}\n",
        flush=True,
    )

    # Open the browser from a daemon thread once the server answers; meanwhile
    # the main thread serves below.
    threading.Thread(
        target=_open_browser_when_ready, args=(port, url), daemon=True
    ).start()

    # Serve in the foreground. Closing the console window (or Ctrl+C) stops the
    # server — single process, so there is no child to orphan.
    run_streamlit_cli(port)


if __name__ == "__main__":
    main()
