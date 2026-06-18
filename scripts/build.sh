#!/usr/bin/env bash
# Regenerate PNG icons from the SVG sources and repackage the .streamDeckPlugin.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
plugin="$root/com.fazal.caffeine.sdPlugin"
icons="$plugin/icons"
assets="$root/assets"
dist="$root/dist"

render() {
  local src="$1" out="$2"
  if command -v rsvg-convert >/dev/null 2>&1; then
    rsvg-convert -w 144 -h 144 "$src" -o "$out"
  elif command -v resvg >/dev/null 2>&1; then
    resvg -w 144 -h 144 "$src" "$out"
  elif command -v inkscape >/dev/null 2>&1; then
    inkscape "$src" -w 144 -h 144 -o "$out" >/dev/null 2>&1
  else
    echo "error: need one of rsvg-convert, resvg, or inkscape to render icons" >&2
    exit 1
  fi
}

echo "Rendering icons..."
render "$assets/cup-on.svg"  "$icons/cup-on.png"
render "$assets/cup-off.svg" "$icons/cup-off.png"
cp "$icons/cup-on.png" "$icons/plugin.png"

echo "Packaging .streamDeckPlugin..."
mkdir -p "$dist"
rm -f "$dist"/*.streamDeckPlugin
( cd "$root" && zip -r -X -q \
    "dist/com.fazal.caffeine.streamDeckPlugin" \
    com.fazal.caffeine.sdPlugin \
    -x '*/__pycache__/*' '*.pyc' )

echo "Done: $dist/com.fazal.caffeine.streamDeckPlugin"
