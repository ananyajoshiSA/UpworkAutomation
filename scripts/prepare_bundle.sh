#!/usr/bin/env bash
# ===========================================================================
#  prepare_bundle.sh - stage the bundled Windows runtime before zipping.
#
#  Run this ONCE on your Mac/Linux box before you zip the folder to send to a
#  client. It places an embeddable Windows Python (and get-pip.py) into
#  runtime/, so the shipped folder carries Python with it. The client therefore
#  NEVER downloads an interpreter (the part antivirus/SmartScreen flags) - their
#  first run only pip-installs the libraries from PyPI (internet needed once).
#
#  runtime/ is git-ignored, so this is not committed; re-run it on a fresh
#  checkout. The downloaded files are Windows binaries - we only stage them
#  here, never execute them on macOS/Linux.
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
