#!/usr/bin/env bash
# Build dist/ankihelper.ankiaddon with vendored runtime deps pinned by uv.lock.
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf dist
mkdir -p dist

# Stage a copy so local, git-ignored files (config.json with real credentials,
# meta.json, user_files/, the dev vendor/ dir) are never packaged.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -R addon/. "$STAGE/"
rm -rf "$STAGE/vendor" "$STAGE/user_files" "$STAGE/meta.json" "$STAGE/config.json"
cp "$STAGE/config.example.json" "$STAGE/config.json"
rm "$STAGE/config.example.json"

# Vendor exactly the locked runtime dependencies (pure Python only).
uv export --no-dev --no-hashes --no-emit-project --quiet \
  | uv pip install --quiet --target "$STAGE/vendor" --python 3.13 -r -

# Their licenses require the license text to travel with every copy, and it lives
# in each *.dist-info/licenses/ (deleted below), so keep it as licenses/<package>/.
mkdir "$STAGE/licenses"
for info in "$STAGE"/vendor/*.dist-info; do
  name="$(basename "$info" .dist-info)"
  if ! find "$info/licenses" -type f 2>/dev/null | grep -q .; then
    echo "error: no license file for vendored $name" >&2
    exit 1
  fi
  mkdir "$STAGE/licenses/$name"
  cp -R "$info/licenses/." "$STAGE/licenses/$name/"
done
# The heatmap is based on Review Heatmap (AGPL-3.0 plus additional terms), which also
# requires its full license text to ship with every copy.
mkdir "$STAGE/licenses/review-heatmap"
cp third_party/review-heatmap/LICENSE "$STAGE/licenses/review-heatmap/LICENSE"
find "$STAGE/vendor" -depth \( -name "*.dist-info" -o -name "__pycache__" -o -name "bin" \) -exec rm -rf {} +
if find "$STAGE/vendor" \( -name "*.so" -o -name "*.pyd" -o -name "*.dylib" \) | grep -q .; then
  echo "error: compiled extensions in vendor/ would break other OSes" >&2
  exit 1
fi

# The add-on's files must sit at the zip root (not inside addon/).
(cd "$STAGE" && zip -qr "$OLDPWD/dist/ankihelper.ankiaddon" . \
  -x "*/__pycache__/*" "*.pyc" ".DS_Store" "meta.json")

# Smoke test: the built zip must import its vendored httpx in isolated mode.
CHECK="$(mktemp -d)"
trap 'rm -rf "$STAGE" "$CHECK"' EXIT
unzip -q dist/ankihelper.ankiaddon -d "$CHECK"
uv run --no-project --python 3.13 python -I -c \
  "import sys; sys.path.insert(0, sys.argv[1]); import httpx; print('vendored httpx', httpx.__version__)" \
  "$CHECK/vendor"
echo "Built dist/ankihelper.ankiaddon"
