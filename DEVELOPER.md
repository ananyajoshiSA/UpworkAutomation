# Developer & Packaging Guide

Everything technical lives here. **End users need none of it** — they double-click
`Start UpworkProposalStrategist` (see [README.md](README.md)). This document
covers how the zero-touch design works, how to run from source, and how to
build/distribute.

---

## 1. How zero-touch works (architecture)

The goal: a non-technical Windows user double-clicks **one** thing and gets a
working desktop app, with **nothing pre-installed** — no Python, conda, pip,
PYTHONPATH, or env vars, ever.

```
Start UpworkProposalStrategist.vbs        ← user double-clicks (no console)
  └─ scripts\run.bat                       ← sets UPS_PACKAGED=1, then…
       ├─ scripts\ensure_runtime.bat       ← FIRST RUN ONLY: builds runtime\
       │     1. download embeddable Python 3.11 (python.org) → runtime\
       │     2. enable site-packages in python311._pth
       │     3. install pip (get-pip.py)
       │     4. pip install -r requirements-windows.txt   (base + desktop)
       │     5. write runtime\.deps_installed   ← success marker (LAST)
       └─ runtime\pythonw.exe desktop_app.py  ← launches the desktop window
             ├─ opens a pywebview (WebView2) window with a splash
             ├─ starts Streamlit headless on a free 127.0.0.1 port (a child
             │   process = this same program re-run with a sentinel flag)
             ├─ waits for the server health endpoint, then loads the app
             └─ on window close, terminates the Streamlit child (no orphans)
```

Key properties:

- **Self-contained runtime.** The interpreter and all dependencies live in
  `runtime\` inside the project folder. Created once, reused forever.
- **Idempotent + self-healing setup.** `ensure_runtime.bat` writes its success
  marker (`runtime\.deps_installed`) **only after every step succeeds**, so an
  interrupted/failed first run leaves no marker and simply resumes on the next
  launch. A completed setup is an instant no-op.
- **No PYTHONPATH.** The project is a proper installable package
  (`pyproject.toml`), and the launcher (`desktop_app.py`) puts the project root
  on `sys.path` for the Streamlit child. Imports work as a package either way.
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

> Internet is needed on the **first** run (to fetch Python + dependency wheels).
> See [Offline / air-gapped install](#5-offline--air-gapped-install) to avoid it.

### The path/runtime/state helpers (load-bearing)

| File | Role |
|------|------|
| `app/paths.py` | The ONE path helper. `resource_base()` (bundled resources, MEIPASS-aware), `state_dir()`/`user_state_dir()` (writable `%APPDATA%`), `is_packaged()`/`is_frozen()`. |
| `app/config.py` | Derives `PROJECT_ROOT`, `STATE_DIR`, `ENV_PATH`, `LOG_DIR` from `app.paths`. |
| `desktop_app.py` | The launcher: free port, Streamlit child (one re-exec code path for dev + frozen), health-gated splash, WebView2 window, clean shutdown. Sets `UPS_PACKAGED=1`. |
| `scripts\ensure_runtime.bat` | First-run embeddable-Python + deps bootstrap. |
| `scripts\run.bat` | Ensure runtime, then launch (normal or `debug`). |
| `Start UpworkProposalStrategist.vbs` | Primary no-console launcher. |
| `Start UpworkProposalStrategist (Debug).bat` | Console + logs for troubleshooting. |

`UPS_PACKAGED=1` is how a non-frozen embeddable run still gets per-user state:
`run.bat` and `desktop_app.py` set it; `app.paths.is_packaged()` honours it
(alongside `sys.frozen`). Dev `streamlit run` leaves it unset → state stays in
the repo root, so tests and local dev are unaffected.

---

## 2. Run from source (developers)

No PYTHONPATH needed anymore. Pick either launch style.

```bash
# Install runtime + desktop deps (one pass; requirements-windows.txt pulls in
# requirements.txt). Optionally also register the package for clean imports.
pip install -r requirements-windows.txt
pip install -e .            # optional: `import app...` from anywhere + the
                            # `upwork-strategist` console command

# A) Desktop window (what the packaged app runs):
python desktop_app.py
#    ...sets UPS_PACKAGED, so state goes to %APPDATA%. Force dev state with:
#    UPS_PACKAGED=0 python desktop_app.py   (mac/linux)   set UPS_PACKAGED=0   (win)

# B) Plain web page in your browser (state stays in the repo root):
python -m streamlit run app/main.py        # no PYTHONPATH required
```

> macOS/Linux: pywebview uses the system WebKit; `pip install pywebview` is
> enough (no pythonnet). The `requirements-windows.txt` pins
> (pywebview + pythonnet) target Windows/WebView2.

### Tests

```bash
pytest -q
```

The suite (389 tests) does not require the desktop deps and runs in dev mode
(state in the repo root).

---

## 3. Distributing the zero-touch folder

This is the model the refactor delivers — **no build step required**:

1. Zip the project folder (the `runtime/` folder is git-ignored and not
   included — it's rebuilt on first run).
2. Send the zip. The user unzips and double-clicks
   `Start UpworkProposalStrategist`.

To give it a real icon, create a Windows shortcut to
`Start UpworkProposalStrategist.vbs`, set its icon to `build_assets\app.ico`, and
place that shortcut on the Desktop.

If `Start UpworkProposalStrategist.vbs` is blocked by antivirus/policy, the user
can double-click `scripts\run.bat` instead (same behaviour, shows a console).

---

## 4. Offline / air-gapped install

The first-run bootstrap normally downloads Python + wheels. To make a fully
offline folder:

1. **Bundle Python:** download `python-3.11.9-embed-amd64.zip` and place it at
   `runtime\python-embed.zip` (the bootstrap uses it instead of downloading).
2. **Bundle wheels:** on a machine with internet,
   `pip download -r requirements-windows.txt -d wheelhouse\`, ship the
   `wheelhouse\` folder, and change the install line in
   `scripts\ensure_runtime.bat` to
   `pip install --no-index --find-links "%ROOT%\wheelhouse" -r "%REQ%"`.
3. Also bundle `get-pip.py` at `runtime\get-pip.py`.

---

## 5. Section-A findings note (from the refactor)

- **PYTHONPATH dependence** was only in the *launch invocation* — the package is
  well-formed (`__init__.py` throughout, absolute `app.x` imports). Fixed by
  `pyproject.toml` (installable package) + the launcher's `sys.path` injection.
- **conda** appeared only in docs; no code dependence. Removed from user docs.
- **Writable state** previously redirected to `%APPDATA%` only when
  `sys.frozen`; the embeddable run isn't frozen, so it would have written into
  the folder. Fixed with the `UPS_PACKAGED` packaged-mode flag.
- **Resource paths** had two helpers and no app code reads bundled data files by
  path (provider-models/skills are pure Python). Unified into `app/paths.py`.
- **No prior `pyproject.toml`** or packaging metadata existed.
- **Runtime decision:** embeddable Python (reasoning in §1).

---

## 6. Verification checklist

Items marked ✅ were verified on this (macOS) dev box; items marked 🪟 are
Windows-only and must be checked on a clean Windows machine (the `.bat`/`.vbs`
bootstrap and the WebView2 window can't run on macOS).

| # | Check | Status |
|---|-------|--------|
| 1 | Fresh Windows, no Python/conda: double-click → one-time setup w/ feedback → window opens | 🪟 |
| 2 | Second double-click: no reinstall, launches in seconds (marker fast-path) | 🪟 |
| 3 | Runs with NO manual PYTHONPATH (imports as a package) | ✅ `pip install -e .` + import from `/tmp` |
| 4 | Spaces in path, and moving the whole folder, still work | ✅ paths resolved from file locations; 🪟 end-to-end |
| 5 | Setup saves API config to `%APPDATA%\...` and persists across restarts/moves | ✅ bootstrap + packaged-gate tests |
| 6 | Bundled data files load in the relocated/bundled runtime | ✅ no path-based data reads; resources via `resource_base()` |
| 7 | Clipboard paste + dossier picker work via the launcher | 🪟 (paste = WebView2 secure-context; upload fallback ✅) |
| 8 | No console on normal run; debug launcher shows logs | 🪟 (`.vbs` style 0 hidden; `(Debug).bat` + `UPS_DEBUG`) |
| 9 | Closing the window leaves no orphan python.exe | ✅ boot harness (server killed); 🪟 exact taskkill |
| 10 | Interrupted first-run setup self-heals next double-click | 🪟 (marker written last) |
| 11 | `SHOW_DEBUG_PANEL` still toggles | ✅ unchanged; 389 tests pass |
| 12 | README's only user step is the double-click; commands live here | ✅ |

To verify the Windows-only items, run `build`-free on a clean VM: copy the
folder, double-click the launcher, and walk the checklist. Use
`Start UpworkProposalStrategist (Debug).bat` to see logs if anything fails.
