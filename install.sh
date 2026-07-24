#!/usr/bin/env bash
# AI Census installer: puts a single `census` command on your PATH.
# Works from a clone (builds locally) or straight from GitHub Releases (gh).
set -euo pipefail

REPO="${CENSUS_REPO:-f-x-dx/ai-census}"
DEST="${CENSUS_DEST:-$HOME/.local/bin}"
mkdir -p "$DEST"

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required (3.9+)" >&2
  exit 1
fi

HERE="$(cd "$(dirname "${0:-.}")" 2>/dev/null && pwd || pwd)"
if [ -f "$HERE/census.py" ]; then
  # running from a clone: build the single file locally
  python3 "$HERE/census.py" dist --out "$DEST/census"
elif command -v curl >/dev/null 2>&1; then
  # no clone: pull the latest release artifact and verify its checksum
  curl -fsSL -o "$DEST/census" "https://github.com/$REPO/releases/latest/download/census.pyz"
  if curl -fsSL -o "$DEST/census.sha256" "https://github.com/$REPO/releases/latest/download/census.pyz.sha256" 2>/dev/null; then
    EXPECT="$(awk '{print $1}' "$DEST/census.sha256")"
    GOT="$(python3 -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$DEST/census")"
    rm -f "$DEST/census.sha256"
    if [ "$EXPECT" != "$GOT" ]; then
      echo "error: checksum mismatch — refusing to install" >&2
      rm -f "$DEST/census"
      exit 1
    fi
  else
    echo "note: no published checksum for this release; verified zip structure only"
  fi
elif command -v gh >/dev/null 2>&1; then
  gh release download -R "$REPO" --pattern census.pyz -O "$DEST/census" --clobber
else
  echo "error: need curl or the GitHub CLI (gh), or run from a clone" >&2
  exit 1
fi

# integrity: refuse a truncated or non-zipapp download before making it executable
if ! python3 - "$DEST/census" <<'PY'
import sys, zipfile
sys.exit(0 if zipfile.is_zipfile(sys.argv[1]) else 1)
PY
then
  echo "error: downloaded artifact is not a valid census.pyz — not installing" >&2
  rm -f "$DEST/census"
  exit 1
fi
chmod +x "$DEST/census"
echo ""
echo "installed: $DEST/census"
python3 - "$DEST/census" <<'PY'
import hashlib, sys
print("sha256:   " + hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())
PY
case ":$PATH:" in
  *":$DEST:"*) ;;
  *) echo "note: add $DEST to your PATH" ;;
esac
echo ""
echo "next:"
echo "  census init      # 3 questions, writes your workspace"
echo "  census check --config census.json"
echo "  census run   --config census.json"
