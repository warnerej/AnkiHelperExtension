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


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_directory_is_private(path):
    TokenStore(path).save("r1", URL)
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="no directory fsync on Windows")
def test_directory_is_synced_after_replace(path, monkeypatch):
    synced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd)))
    TokenStore(path).save("r1", URL)
    assert len(synced) == 2  # the file, then its directory


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


def test_save_reports_failure(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert TokenStore(blocker / "session.json").save("t", "http://x") is False
    assert TokenStore(tmp_path / "ok" / "session.json").save("t", "http://x") is True
