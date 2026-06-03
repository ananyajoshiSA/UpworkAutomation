# Developer & Packaging Guide

Everything technical lives here. **End users need none of it** — they double-click
`Start Upwork Proposal Strategist` (see [README.md](README.md)). This document
covers how the browser-app design works, how to run from source, and how to
build/distribute.

---

## 1. How it works (architecture)

The goal: a non-technical Windows user double-clicks **one** thing and the app
opens in their **web browser**, with **nothing pre-installed** — no Python,
conda, pip, PYTHONPATH, or env vars, ever.

Python travels *inside* the folder (staged by `scripts\prepare_bundle.sh` before
you zip), so the client never downloads an interpreter — only the app's
libraries, from PyPI, on the first run.

```
Start Upwork Proposal Strategist.bat       ← user double-clicks (console = "running" light)
  └─ scripts\run.bat                        ← sets UPS_PACKAGED=1, then…
       ├─ scripts\ensure_runtime.bat        ← FIRST RUN ONLY:
       │     1. (Python is already in runtime\ — bundled, not downloaded)
       │     2. enable site-packages in python311._pth
       │     3. install pip from the bundled get-pip.py
       │     4. pip install -r requirements.txt   (from PyPI; internet)
       │     5. write runtime\.deps_installed   ← success marker (LAST)
       └─ runtime\python.exe desktop_app.py  ← runs in the FOREGROUND (this console)
             ├─ picks a free 127.0.0.1 port; starts Streamlit headless, in-process
             ├─ a daemon thread waits for /_stcore/health, then opens the browser
             └─ closing the console window stops the server (one process; no orphan)
```

Key properties:

- **Bundled interpreter, libraries on first run.** `runtime\python.exe` ships
  inside the folder; `ensure_runtime.bat` only installs the Python libraries
  (once). There is **no "download an interpreter from the web" step** — that
  download-then-execute pattern is what antivirus/SmartScreen flags. The old
  `pywebview` / `pythonnet` / WebView2 stack is gone: the UI is just the browser.
- **Idempotent + self-healing setup.** `ensure_runtime.bat` writes its success
  marker (`runtime\.deps_installed`) **only after every step succeeds**, so an
  interrupted/failed first run leaves no marker and simply resumes on the next
  launch. A completed setup is an instant no-op.
- **Single process, clean shutdown.** `desktop_app.py` *is* the Streamlit server
  (its stable CLI is driven in-process) and runs in the foreground. Closing the
  console window stops it — nothing to taskkill, no orphaned `python.exe`.
- **No PYTHONPATH.** The project is a proper installable package
  (`pyproject.toml`), and the launcher (`desktop_app.py`) puts the project root
  on `sys.path` before handing off to Streamlit.
- **Writable state is outside the folder.** The API-key `.env` and logs live in
  `%APPDATA%\UpworkProposalStrategist\`, so the install folder can be read-only
  and can be moved/renamed without losing settings. See `app/paths.py`.
- **Move-safe.** All paths are resolved at runtime from file locations and
  `%APPDATA%` — never baked in — so moving the whole folder still works.

### Which runtime: embeddable Python (not a venv)

I used **python.org's embeddable Python 3.11 zip**, not an auto-created venv.
Why: the embeddable build has **zero dependence on any system Python** (a venv
needs a base interpreter to create it), it's a single self-contained folder, and
it's the lightest way to get "nothing pre-installed." The one quirk it requires —
enabling `site` / `Lib\site-packages` via the `._pth` file so pip-installed
packages import — is handled deterministically in `ensure_runtime.bat`.

`scripts\prepare_bundle.sh` stages this interpreter into `runtime\` from your Mac
(it just downloads and unzips the Windows build and `get-pip.py`; nothing is
executed on macOS).

> Internet is needed on the **first** run only, to fetch the dependency wheels
> from PyPI. Python itself is bundled. See [Offline / air-gapped
> install](#4-offline--air-gapped-install) to remove the first-run download too.

### The path/runtime/state helpers (load-bearing)

| File | Role |
|------|------|
| `app/paths.py` | The ONE path helper. `resource_base()` (bundled resources, MEIPASS-aware), `state_dir()`/`user_state_dir()` (writable `%APPDATA%`), `is_packaged()`/`is_frozen()`. |
| `app/config.py` | Derives `PROJECT_ROOT`, `STATE_DIR`, `ENV_PATH`, `LOG_DIR` from `app.paths`. |
| `desktop_app.py` | The launcher: free port, in-process headless Streamlit, health-gated browser open, foreground serve. Sets `UPS_PACKAGED=1`. |
| `scripts\prepare_bundle.sh` | Dev/**macOS/Linux**: stage the embeddable Python + `get-pip.py` into `runtime\` before zipping. |
| `scripts\prepare_bundle.bat` | Dev/**Windows**: same, for running a repo clone directly on Windows (downloads via PowerShell). |
| `scripts\ensure_runtime.bat` | First-run: enable site-packages, install pip, pip-install the libraries. |
| `scripts\run.bat` | Set `UPS_PACKAGED=1`, ensure runtime, then run in the foreground (normal or `debug`). |
| `Start Upwork Proposal Strategist.bat` | Primary launcher (opens the browser; the console is the run indicator). |
| `Start Upwork Proposal Strategist (Debug).bat` | Same app, with full logs for troubleshooting. |

`UPS_PACKAGED=1` is how a non-frozen embeddable run still gets per-user state:
`run.bat` and `desktop_app.py` set it; `app.paths.is_packaged()` honours it
(alongside `sys.frozen`). Dev `streamlit run` leaves it unset → state stays in
the repo root, so tests and local dev are unaffected.

---

## 2. Run from source (developers)

No PYTHONPATH needed. Pick either launch style.

```bash
# Install the libraries.
pip install -r requirements.txt
pip install -e .            # optional: `import app...` from anywhere + the
                            # `upwork-strategist` console command

# A) Exactly what the packaged app runs (opens your default browser):
python desktop_app.py
#    ...sets UPS_PACKAGED, so state goes to %APPDATA% (or
#    ~/.upwork_proposal_strategist on macOS/Linux). Force dev state with:
#    UPS_PACKAGED=0 python desktop_app.py   (mac/linux)   set UPS_PACKAGED=0   (win)

# B) Plain Streamlit (state stays in the repo root):
python -m streamlit run app/main.py
```

There is no longer a separate "native window" vs "browser" mode — both open in
the browser. `pywebview` and `pythonnet` were removed.

### Tests

```bash
pytest -q
```

The suite runs in dev mode (state in the repo root) and needs only
`requirements.txt` — no desktop/GUI packages.

---

## 3. Distributing the folder

**One command builds a clean, ready-to-send zip:**

```bash
bash scripts/package.sh
```

It stages the bundled Python, copies the project while **excluding your secrets
and dev junk** (`.env` / API key, `.git`, `.vscode`, `.claude`, caches, `logs`,
`tests`, your personal `sample_dossier` files), writes
`dist/UpworkProposalStrategist.zip`, and **aborts if a `.env` ever lands inside**.
Re-run it after any change.

**Send `dist/UpworkProposalStrategist.zip`.** The user unzips and double-clicks
`Start Upwork Proposal Strategist`. Their first run installs the libraries from
PyPI (internet needed once), then the app opens in their browser.

> Lower-level helper: `scripts/prepare_bundle.sh` only stages `runtime\` (the
> bundled Python + `get-pip.py`) — `package.sh` calls it for you. `runtime\` is
> git-ignored, so it is re-staged on a fresh checkout.

> ⚠️ **The GitHub repo is source-only and is NOT directly runnable.** `runtime\`
> (the bundled Python) is git-ignored, so a `git clone` / "Download ZIP" has no
> `python.exe`, and the launcher reports *"runtime files are missing"*. To run
> from a repo checkout, stage the runtime first: **macOS/Linux** →
> `bash scripts/prepare_bundle.sh`; **Windows** → double-click
> `scripts\prepare_bundle.bat`. **End users/clients must always receive the
> packaged `UpworkProposalStrategist.zip`** (it contains `runtime\`), never the
> GitHub download.

To give it a real icon, create a Windows shortcut to
`Start Upwork Proposal Strategist.bat`, set its icon to `build_assets\app.ico`,
and place that shortcut on the Desktop.

If antivirus/policy blocks the launcher, the user can double-click
`scripts\run.bat` directly (same behaviour).

> **Why this is friendlier to antivirus than the old build:** the previous
> version used a hidden-launch `.vbs` *and* downloaded a Python interpreter at
> runtime via PowerShell — both classic malware-heuristic / ASR triggers. This
> version ships a plain `.bat`, bundles Python (no interpreter download), and
> only lets `pip` fetch library wheels from PyPI (ordinary, not flagged the same
> way). Note: unsigned code can still trip SmartScreen ("More info → Run
> anyway"); the only real cure for that is code-signing, which is out of scope
> here.

---

## 4. Offline / air-gapped install

The default already **bundles Python**, so the only thing a client downloads is
the library wheels on first run. To make the folder **fully offline** (no
internet even on the first run):

1. **Bundle wheels:** on a machine with internet,
   `pip download -r requirements.txt -d wheelhouse\` (from a Mac, add
   `--platform win_amd64 --python-version 311 --only-binary=:all:` to fetch the
   Windows wheels), ship the `wheelhouse\` folder, and change the install line in
   `scripts\ensure_runtime.bat` to:
   `"%PYEXE%" -m pip install --no-index --find-links "%ROOT%\wheelhouse" -r "%REQ%"`
2. Python and `get-pip.py` are already staged by `scripts\prepare_bundle.sh`, so
   nothing else is needed.

---

## 5. Design notes

- **Browser, not a native window.** The app opens in the default browser instead
  of a pywebview/WebView2 window; `pywebview` + `pythonnet` were removed (fewer
  moving parts, and `pythonnet`'s .NET interop was itself an extra antivirus
  trigger). `127.0.0.1` is a browser "secure context", so clipboard paste on the
  Job Screenshot screen still works; the file-upload control is the always-on
  fallback. The console window is both the "running" indicator and the
  clean-shutdown mechanism — close it and the single server process stops.
  Streamlit binds to `127.0.0.1` **only** (verified with `lsof`: `TCP
  127.0.0.1:<port> (LISTEN)`), so no network-facing port is ever exposed. A
  first-run Windows Defender Firewall prompt for `python.exe` is therefore
  harmless — a loopback listener works whether the user clicks **Allow access**
  or **Cancel**. There is no clean non-admin way to suppress the prompt, so the
  user docs just reassure (click Allow). Don't change the bind to `0.0.0.0`.
- **PYTHONPATH dependence** was only in the *launch invocation* — the package is
  well-formed (`__init__.py` throughout, absolute `app.x` imports). Fixed by
  `pyproject.toml` (installable package) + the launcher's `sys.path` injection.
- **conda** appeared only in docs; no code dependence.
- **Writable state** previously redirected to `%APPDATA%` only when
  `sys.frozen`; the embeddable run isn't frozen, so it would have written into
  the folder. Fixed with the `UPS_PACKAGED` packaged-mode flag.
- **Runtime decision:** embeddable Python (reasoning in §1).

---

## 6. Verification checklist

Items marked ✅ were verified on this (macOS) dev box; items marked 🪟 are
Windows-only and must be checked on a clean Windows machine (the `.bat`
bootstrap and the bundled `python.exe` can't run on macOS).

| # | Check | Status |
|---|-------|--------|
| 1 | Fresh Windows, no Python: double-click → one-time library install w/ feedback → browser opens | 🪟 |
| 2 | Second double-click: no reinstall, opens in seconds (marker fast-path) | 🪟 |
| 3 | Runs with NO manual PYTHONPATH (imports as a package) | ✅ `pip install -e .` + import from `/tmp` |
| 4 | Spaces in path, and moving the whole folder, still work | ✅ paths resolved from file locations; 🪟 end-to-end |
| 5 | Setup saves API config to `%APPDATA%\...` and persists across restarts/moves | ✅ bootstrap + packaged-gate tests |
| 6 | Bundled data files load in the relocated/bundled runtime | ✅ no path-based data reads; resources via `resource_base()` |
| 7 | Clipboard paste + dossier picker work in the browser | 🪟 (paste = 127.0.0.1 secure context; upload fallback ✅) |
| 8 | Console stays open as the run indicator; debug launcher shows logs | 🪟 (`UPS_DEBUG=1` raises the log level) |
| 9 | Closing the console leaves no orphan `python.exe` | ✅ single process; 🪟 end-to-end |
| 10 | Interrupted first-run setup self-heals next double-click | 🪟 (marker written last) |
| 11 | `prepare_bundle.sh` stages `runtime\` (python.exe + get-pip.py) | ✅ runs on macOS |
| 12 | `SHOW_DEBUG_PANEL` still toggles | ✅ unchanged; tests pass |

To verify the Windows-only items, run `build`-free on a clean VM: stage the
bundle, copy the folder, double-click the launcher, and walk the checklist. Use
`Start Upwork Proposal Strategist (Debug).bat` to see logs if anything fails.
