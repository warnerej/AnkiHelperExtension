import json
import os
import stat

import pytest

from addon.token_store import TokenStore

URL = "http://localhost:8000"


@pytest.fixture
def path(tmp_path):
    return tmp_path / "user_files" / "session.json"


def test_roundtrip_creates_parent_dir(path):
    store = TokenStore(path)
    store.save("r1", URL)
    assert TokenStore(path).load(URL) == "r1"


def test_missing_file(path):
    assert TokenStore(path).load(URL) is None


def test_save_overwrites(path):
    store = TokenStore(path)
    store.save("r1", URL)
    store.save("r2", URL)
    assert store.load(URL) == "r2"
    assert [p.name for p in path.parent.iterdir()] == ["session.json"]


def test_other_server_token_ignored_but_kept(path):
    store = TokenStore(path)
    store.save("r1", URL)
    assert store.load("https://other.io") is None
    assert store.load(URL) == "r1"


@pytest.mark.parametrize("content", ["not json", "[]", '{"refresh_token": 5}', "{}"])
def test_corrupt_file_is_cleared(path, content):
    path.parent.mkdir()
    path.write_text(content)
    assert TokenStore(path).load(URL) is None
    assert not path.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_file_is_private(path):
    TokenStore(path).save("r1", URL)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_failed_replace_keeps_old_token_and_no_temp(path, monkeypatch):
    store = TokenStore(path)
    store.save("old", URL)

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    store.save("new", URL)  # logged, not raised
    assert json.loads(path.read_text())["refresh_token"] == "old"
    assert [p.name for p in path.parent.iterdir()] == ["session.json"]


def test_clear_is_idempotent(path):
    store = TokenStore(path)
    store.save("r1", URL)
    store.clear()
    store.clear()
    assert store.load(URL) is None
