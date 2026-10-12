# AnkiHelperExtension

Anki add-on client for AnkiHelper.

## Setup

1. Copy `addon/config.example.json` to `addon/config.json` (this file is git-ignored, so your credentials stay local).
2. Edit `addon/config.json`: set `base_url`, `email` and `password`. `http` is only allowed for `localhost`.
3. Install the vendored dependencies: `uv pip install --target addon/vendor --python 3.13 "httpx>=0.28.1" "pydantic>=2.14.1"`.
4. With Anki closed, link the add-on: `ln -s "$PWD/addon" ~/Library/Application\ Support/Anki2/addons21/ankihelper`.
5. In Anki, use Tools > AnkiHelper: Test connection.

`scripts/package.sh` builds `dist/ankihelper.ankiaddon` using the example config, never your local `config.json`.
