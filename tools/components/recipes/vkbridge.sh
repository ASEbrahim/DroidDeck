#!/usr/bin/env bash
set -euo pipefail
. tools/malibridge/release.env
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
bash tools/packs/fetch-verified.sh \
  "https://github.com/${MALIBRIDGE_REPO}/releases/download/${MALIBRIDGE_TAG}/malibridge-droiddeck.tar.zst" \
  "$MALIBRIDGE_SHA256" "$work/malibridge-droiddeck.tar.zst"
python3 tools/malibridge/place.py "$work/malibridge-droiddeck.tar.zst"
