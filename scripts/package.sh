#!/usr/bin/env bash
# Build dist/ankihelper.ankiaddon with vendored runtime deps.
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf addon/vendor dist
mkdir -p dist
uv pip install --target addon/vendor --python 3.13 "httpx>=0.28.1"

# Stage a copy so the local (git-ignored) config.json with real credentials is never packaged.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -R addon/. "$STAGE/"
cp "$STAGE/config.example.json" "$STAGE/config.json"
rm "$STAGE/config.example.json"

# The add-on's files must sit at the zip root (not inside addon/).
(cd "$STAGE" && zip -r "$OLDPWD/dist/ankihelper.ankiaddon" . \
  -x "__pycache__/*" "*/__pycache__/*" "user_files/*" "vendor/bin/*" "*.pyc" ".DS_Store")
echo "Built dist/ankihelper.ankiaddon"
