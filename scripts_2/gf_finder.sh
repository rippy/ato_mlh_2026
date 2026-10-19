#!/usr/bin/env bash
# gf_finder.sh - gluten-free restaurants near the Raleigh Convention Center
#
#   ./gf_finder.sh             fetch anything not cached yet, then build map.html
#   ./gf_finder.sh --refresh   re-download everything first (other fetch_data.py flags work too)
#
# Override the location or radius:  RADIUS_M=2500 ./gf_finder.sh
set -euo pipefail
cd "$(dirname "$0")"

python3 fetch_data.py "$@"
echo "== Build the map =="
python3 gf_map.py
echo
echo "Serve it (OSM map tiles need a Referer, which file:// pages don't send):"
echo "  cd \"$(pwd)\" && python3 -m http.server 8000"
echo "Then open http://localhost:8000/map.html"
