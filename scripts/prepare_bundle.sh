#!/usr/bin/env bash
# ===========================================================================
#  prepare_bundle.sh - (re)create the bundled Windows runtime in runtime/.
#
#  You normally DON'T need this: runtime/ (the embeddable Windows Python +
#  get-pip.py) is committed to the repo, so a fresh checkout already has it. Use
#  this only to rebuild a deleted/corrupted runtime, or to bump the pinned Python
#  version (edit PYVER, delete runtime/, then re-run).
#
#  It downloads + unzips the embeddable Windows Python and get-pip.py into
#  runtime/. The files are Windows binaries - we only stage them here, never
#  execute them on macOS/Linux. The client never downloads an interpreter (the
#  part antivirus/SmartScreen flags); their first run only pip-installs the
#  libraries from PyPI (internet needed once).
#
#  Requirements: curl and unzip (both ship with macOS).
#  Usage:        bash scripts/prepare_bundle.sh
# ===========================================================================
set -euo pipefail

PYVER="3.11.9"   # keep in step with scripts/ensure_runtime.bat (python311._pth)
PYZIP="python-${PYVER}-embed-amd64.zip"
PYURL="https://www.python.org/ftp/python/${PYVER}/${PYZIP}"
GETPIP_URL="https://bootstrap.pypa.io/get-pip.py"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RT="${ROOT}/runtime"

mkdir -p "${RT}"

# --- 1. Embeddable Python -> runtime/ --------------------------------------
if [ -f "${RT}/python.exe" ]; then
  echo "[prepare] runtime/python.exe already present - skipping Python download."
else
  echo "[prepare] Downloading embeddable Python ${PYVER} (Windows)..."
  TMPZIP="${RT}/.python-embed.zip"
  curl -fSL "${PYURL}" -o "${TMPZIP}"
  echo "[prepare] Extracting into runtime/ ..."
  unzip -o -q "${TMPZIP}" -d "${RT}"
  rm -f "${TMPZIP}"
fi

# --- 2. get-pip.py -> runtime/ ---------------------------------------------
if [ -f "${RT}/get-pip.py" ]; then
  echo "[prepare] runtime/get-pip.py already present - skipping."
else
  echo "[prepare] Downloading get-pip.py..."
  curl -fSL "${GETPIP_URL}" -o "${RT}/get-pip.py"
fi

echo "[prepare] runtime/ staged ($(ls -1 "${RT}" | wc -l | tr -d ' ') files, incl. python.exe + get-pip.py)."
echo "[prepare] To build a clean, ready-to-send zip, run:  bash scripts/package.sh"
