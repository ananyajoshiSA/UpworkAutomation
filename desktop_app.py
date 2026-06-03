"""Native desktop launcher for the Upwork Proposal Strategist.

What this does
--------------
The app is a local Streamlit server. This launcher wraps it in a native
desktop window so a non-technical user never touches a terminal:

1. Pick a FREE local port (never hard-code 8501, so two copies don't collide).
2. Start Streamlit **headless**, bound to 127.0.0.1 on that port, as a child
   process. The child is THIS SAME program re-executed with a sentinel flag —
   so it works identically in a dev checkout (``python desktop_app.py``) and in
   a frozen PyInstaller build where there is no separate ``python.exe`` to call.
3. Show a lightweight splash, poll the server's health endpoint, and only swap
   the window to the real app once the server actually answers (the first
   packaged launch is slow — this avoids a blank/early window).
4. Open a pywebview window (WebView2 on Windows) pointing at the local URL.
5. On window close, terminate the Streamlit child — no orphan process.

Design notes
------------
* ONE code path for dev and frozen: ``start_streamlit`` re-execs this file with
  ``RUN_FLAG``; the child runs :func:`run_streamlit_cli`, which drives
  Streamlit's own CLI in-process. Streamlit's CLI is stable across versions, so
  this survives Streamlit upgrades better than calling its internal bootstrap.
* The parent process imports ``webview`` lazily and the child never imports it,
  so the GUI layer is only loaded where it's needed.
* Writable state (``.env``, logs) is handled by :mod:`app.config`, which
  redirects to a per-user location when packaged — see DEVELOPER.md.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# The one shared path helper (stdlib-only, no import cycle). `app` is always
# importable here: as a dev script its directory (the repo root) is on
# sys.path[0]; when frozen, the `app` package is in the PyInstaller archive.
from app.paths import PACKAGED_ENV_FLAG, resource_base


# Mirrors app.config.APP_TITLE. Kept as a literal so the parent process can set
# the window title without importing the (heavier) app package first.
APP_TITLE = "Upwork Proposal Strategist"

# Sentinel argv flag that puts a re-executed copy of this program into
# "run the Streamlit server" mode instead of "open a window" mode.
RUN_FLAG = "__ups_run_streamlit__"

# How long to wait for the Streamlit server to answer before giving up. The
# first packaged launch unpacks + imports a lot, and antivirus can slow the
# very first run, so this is deliberately generous.
BOOT_TIMEOUT = 90.0

# Windows process-creation flags (see start_streamlit / stop_streamlit).
_CREATE_NO_WINDOW = 0x08000000        # child server must not flash a console
_CREATE_NEW_PROCESS_GROUP = 0x00000200  # lets us kill the whole tree cleanly


# ---------------------------------------------------------------------------
# Resource / path resolution
# ---------------------------------------------------------------------------
# resource_base() is imported from app.paths — the ONE shared helper — so the
# launcher, app.config, and the Streamlit child resolve paths identically in
# dev, in the embeddable-Python runtime, and inside a PyInstaller bundle.


def _main_script() -> Path:
    """Absolute path to the Streamlit entry script Streamlit will execute."""
    return resource_base() / "app" / "main.py"


# ---------------------------------------------------------------------------
# Child process: run the Streamlit server in-process via its own CLI
# ---------------------------------------------------------------------------


def run_streamlit_cli(port: int) -> None:
    """Run the Streamlit server (headless) on ``port``. Never returns normally.

    Invoked only in the re-executed child process. It points Streamlit at the
    bundled ``app/main.py`` and pins every server option on the command line so
    behavior does not depend on a config file being found. Working directory is
    set to the resource base so ``import app...`` resolves and Streamlit still
    picks up the bundled ``.streamlit/config.toml`` (theme, toolbar).
    """
    base = resource_base()
    # CWD so Streamlit finds .streamlit/config.toml; sys.path so `import app`
    # works when Streamlit execs app/main.py.
    os.chdir(str(base))
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))

    sys.argv = [
        "streamlit",
        "run",
        str(_main_script()),
        "--server.address=127.0.0.1",
        f"--server.port={port}",
        "--server.headless=true",          # never try to open a browser
        "--browser.gatherUsageStats=false",  # local-first: no telemetry call
        "--server.fileWatcherType=none",   # frozen app: nothing to hot-reload
        "--server.runOnSave=false",
        # Local single-user desktop context: these clear the 403 upload error
        # WebView2/local proxies otherwise trigger on st.file_uploader. Mirrors
        # the bundled .streamlit/config.toml.
        "--server.enableCORS=false",
        "--server.enableXsrfProtection=false",
        "--server.maxUploadSize=20",
    ]
    # Imported here (not at module top) so the GUI-only parent never pays for
    # importing Streamlit, and the child never imports the webview GUI lib.
    from streamlit.web import cli as stcli

    sys.exit(stcli.main())


# ---------------------------------------------------------------------------
# Parent process: spawn / health-check / stop the server child
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


def _child_command(port: int) -> list[str]:
    """Command that re-executes THIS program in Streamlit-server mode.

    Frozen: ``<app>.exe RUN_FLAG <port>`` (the exe IS the entry point).
    Dev:    ``python desktop_app.py RUN_FLAG <port>``.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, RUN_FLAG, str(port)]
    return [sys.executable, os.path.abspath(__file__), RUN_FLAG, str(port)]


def start_streamlit(port: int) -> subprocess.Popen:
    """Launch the Streamlit server child bound to ``port`` and return its handle."""
    kwargs: dict = {}
    if os.name == "nt":
        # Own process group so the whole tree can be terminated on shutdown.
        flags = _CREATE_NEW_PROCESS_GROUP
        # Hide the server's console on a normal run; the debug launcher sets
        # UPS_DEBUG=1 so the Streamlit/Python log stays visible for diagnosis.
        if os.environ.get("UPS_DEBUG", "") != "1":
            flags |= _CREATE_NO_WINDOW
        kwargs["creationflags"] = flags
    return subprocess.Popen(_child_command(port), **kwargs)


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


def wait_until_healthy(
    port: int, proc: subprocess.Popen, timeout: float = BOOT_TIMEOUT
) -> bool:
    """Poll the server until it is healthy, it dies, or ``timeout`` elapses.

    Returns False immediately if the child process exits (a failed boot), so the
    caller can show an error instead of spinning for the full timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False  # server process died during startup
        if _health_ok(port):
            return True
        time.sleep(0.4)
    return False


def stop_streamlit(proc: subprocess.Popen | None) -> None:
    """Terminate the Streamlit child (and any grandchildren). Idempotent."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            # /T kills the whole process tree, /F forces it — guarantees no
            # orphaned server process is left running after the window closes.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
            )
        else:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    except Exception:  # noqa: BLE001 - last-resort kill; never raise on shutdown
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# First-run setup
# ---------------------------------------------------------------------------


def _bootstrap_env() -> None:
    """Create the per-user ``.env`` from the bundled template on first run.

    The write target comes from :data:`app.config.ENV_PATH`, which already
    redirects to ``%APPDATA%\\UpworkProposalStrategist`` when frozen, so the
    Setup screen and this bootstrap always agree on one location.
    """
    try:
        from app.config import ENV_PATH  # frozen-aware target
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
# Window chrome (splash + error states shown inside the native window)
# ---------------------------------------------------------------------------


def _loading_html(message: str) -> str:
    """Branded full-window HTML used for the splash and error states."""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{APP_TITLE}</title>
<style>
  html,body {{ height:100%; margin:0; font-family:'Segoe UI',Arial,sans-serif;
    background:linear-gradient(180deg,#F4F7FD 0%,#E9EFFA 100%); color:#101828; }}
  .wrap {{ height:100%; display:flex; flex-direction:column; align-items:center;
    justify-content:center; gap:1.1rem; }}
  .title {{ font-size:1.5rem; font-weight:700; letter-spacing:-0.01em; }}
  .msg {{ color:#667085; font-size:0.95rem; }}
  .spinner {{ width:38px; height:38px; border:4px solid #C7D9FB;
    border-top-color:#2563EB; border-radius:50%; animation:spin 0.9s linear infinite; }}
  @keyframes spin {{ to {{ transform:rotate(360deg); }} }}
  @media (prefers-reduced-motion: reduce) {{ .spinner {{ animation:none; }} }}
</style></head>
<body><div class="wrap">
  <div class="spinner"></div>
  <div class="title">{APP_TITLE}</div>
  <div class="msg">{message}</div>
</div></body></html>"""


SPLASH_HTML = _loading_html("Starting up — this can take a moment on first launch…")


def _error_html() -> str:
    return _loading_html(
        "The app couldn't start its local engine. "
        "Please close this window and try again."
    ).replace(
        '<div class="spinner"></div>',
        '<div style="font-size:34px">⚠️</div>',
    )


def _native_error(message: str) -> None:
    """Show a native message box on Windows (no console in the windowed build)."""
    if os.name == "nt":
        try:
            import ctypes  # stdlib; only meaningful on Windows

            ctypes.windll.user32.MessageBoxW(0, message, APP_TITLE, 0x10)
            return
        except Exception:  # noqa: BLE001
            pass
    # Dev / non-Windows: stderr is visible.
    print(f"{APP_TITLE}: {message}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    # Declare packaged-desktop mode (unless explicitly overridden) so
    # app.config redirects writable state — the API-key .env and logs — to
    # %APPDATA% instead of the read-only / relocatable install folder. This
    # process AND the Streamlit child it spawns (which inherits the
    # environment) both see it. A developer can force dev behaviour with
    # UPS_PACKAGED=0.
    os.environ.setdefault(PACKAGED_ENV_FLAG, "1")

    # Child mode: run the server and nothing else (never opens a window).
    if RUN_FLAG in sys.argv:
        try:
            port = int(sys.argv[sys.argv.index(RUN_FLAG) + 1])
        except (ValueError, IndexError):
            port = 8501
        run_streamlit_cli(port)
        return

    # Parent (window) mode.
    _bootstrap_env()
    port = pick_free_port()
    proc = start_streamlit(port)
    url = f"http://127.0.0.1:{port}"

    try:
        import webview  # lazy: only the parent needs the GUI library
    except Exception as exc:  # noqa: BLE001
        stop_streamlit(proc)
        _native_error(f"Failed to load the window library (pywebview).\n\n{exc}")
        return

    window = webview.create_window(
        APP_TITLE,
        html=SPLASH_HTML,
        width=1280,
        height=860,
        min_size=(900, 640),
        resizable=True,
    )

    def _boot(win) -> None:
        # Runs on a worker thread after the GUI loop is up. Swap the splash for
        # the live app only once the server actually answers.
        if wait_until_healthy(port, proc):
            win.load_url(url)
        else:
            win.load_html(_error_html())

    try:
        start_kwargs: dict = {}
        if os.name == "nt":
            # Force the WebView2 (Edge Chromium) backend — the decided runtime.
            # Falling back to legacy MSHTML/IE would not render the app.
            start_kwargs["gui"] = "edgechromium"
        webview.start(_boot, window, **start_kwargs)
    except Exception as exc:  # noqa: BLE001 - surface, then always clean up
        _native_error(
            "The app window could not open.\n\n"
            "This usually means the Microsoft Edge WebView2 runtime is missing. "
            "Install it from https://developer.microsoft.com/microsoft-edge/webview2/ "
            f"and try again.\n\nDetails: {exc}"
        )
    finally:
        # Whether the user closed the window or startup failed, never leave the
        # Streamlit server running.
        stop_streamlit(proc)


if __name__ == "__main__":
    # Required for frozen Windows builds: stops a child process re-importing the
    # entry module from spawning a second app instance.
    import multiprocessing

    multiprocessing.freeze_support()
    main()
