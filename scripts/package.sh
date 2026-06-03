#!/usr/bin/env bash
# ===========================================================================
#  package.sh - build a clean, ready-to-send client ZIP in ONE command.
#
#  Does everything for you, so nothing has to be excluded by hand:
#    1. stages the bundled Windows Python into runtime\ (via prepare_bundle.sh),
#    2. copies the project into a clean folder, EXCLUDING your secrets and
#       dev-machine junk (.env / API key, .git, .vscode, .claude, caches, logs,
#       tests, your personal sample_dossier files…),
#    3. zips it to dist/UpworkProposalStrategist.zip,
#    4. verifies the zip contains NO .env before declaring success.
#
#  Send dist/UpworkProposalStrategist.zip as-is.
#
#  Requirements: rsync, zip, curl, unzip (all ship with macOS).
#  Usage:        bash scripts/package.sh
# ===========================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

NAME="UpworkProposalStrategist"          # client-facing folder + zip name
DIST="${ROOT}/dist"
STAGE="${DIST}/${NAME}"
ZIP="${DIST}/${NAME}.zip"

# --- 1. Make sure the bundled runtime is staged ----------------------------
bash "${SCRIPT_DIR}/prepare_bundle.sh"

# --- 2. Clean staging copy (exclude secrets + dev junk) --------------------
echo
echo "[package] Building a clean copy (excluding your .env / API key and dev files)..."
rm -rf "${STAGE}"
mkdir -p "${STAGE}"

rsync -a \
  --exclude '.git/' \
  --exclude '.gitignore' \
  --exclude '.env' \
  --exclude '.env.tmp' \
  --exclude '.env.local' \
  --exclude '.env.production' \
  --exclude '.vscode/' \
  --exclude '.claude/' \
  --exclude '.DS_Store' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.pytest_cache/' \
  --exclude '.mypy_cache/' \
  --exclude '.ruff_cache/' \
  --exclude 'logs/' \
  --exclude 'dist/' \
  --exclude 'tests/' \
  --exclude 'sample_dossier/' \
  --exclude '.python-embed.zip' \
  "${ROOT}/" "${STAGE}/"

# Re-add the two things we DO want that live under excluded patterns/paths:
#   - .env.example: the template the app copies to create the user's .env.
#   - the dossier TEMPLATE only (never the user's personal sample files).
cp "${ROOT}/.env.example" "${STAGE}/.env.example"
mkdir -p "${STAGE}/sample_dossier"
cp "${ROOT}/sample_dossier/TEMPLATE_dossier.json" "${STAGE}/sample_dossier/TEMPLATE_dossier.json"

# Safety net: never ship a real .env even if one ever slipped through.
rm -f "${STAGE}/.env"

# --- 3. Zip it -------------------------------------------------------------
echo "[package] Zipping..."
rm -f "${ZIP}"
( cd "${DIST}" && zip -r -q -X "${NAME}.zip" "${NAME}" )
rm -rf "${STAGE}"

SIZE="$(du -h "${ZIP}" | cut -f1 | tr -d ' ')"

# --- 4. Verify no secret leaked into the zip -------------------------------
if unzip -l "${ZIP}" | grep -Eq '/\.env$'; then
  echo "[package] !! WARNING: a .env file is inside the zip - DO NOT SEND IT." >&2
  exit 1
fi

echo
echo "[package] Done  ->  dist/${NAME}.zip  (${SIZE})"
echo "[package] Verified: no .env (API key) inside. Safe to send."
echo
echo "Send dist/${NAME}.zip to your client. They unzip it and double-click"
echo "\"Start Upwork Proposal Strategist\". Re-run this script after any change."
