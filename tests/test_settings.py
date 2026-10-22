import pytest

from addon.errors import NotConfiguredError
from addon.settings import Settings, strip_friends, strip_legacy_credentials


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"base_url": "http://example.com"},
        {"base_url": "ftp://x"},
        {"base_url": "https://user:pw@example.com"},
        {"base_url": "https://user@example.com"},
    ],
)
def test_settings_validation(config):
    with pytest.raises(NotConfiguredError):
        Settings.from_config(config)


def test_settings_ok_needs_only_base_url():
    s = Settings.from_config(
        {"base_url": "http://localhost:8000/", "timeout_seconds": "bad"}
    )
    assert s.base_url == "http://localhost:8000" and s.timeout_seconds == 60


def test_strip_legacy_credentials():
    config = {
        "base_url": "https://x.io",
        "email": "a",
        "password": "p",
        "username": "u",
    }
    cleaned, had_password = strip_legacy_credentials(config)
    assert cleaned == {"base_url": "https://x.io"} and had_password
    assert config["password"] == "p"  # input untouched
    cleaned, had_password = strip_legacy_credentials({"base_url": "https://x.io"})
    assert cleaned == {"base_url": "https://x.io"} and not had_password
    assert strip_legacy_credentials({"email": "a", "password": ""}) == ({}, False)


def test_strip_friends():
    cleaned, had = strip_friends({"base_url": "x", "friends": ["a"]})
    assert cleaned == {"base_url": "x"} and had
    assert strip_friends({"base_url": "x", "friends": []}) == ({"base_url": "x"}, False)
    assert strip_friends({"base_url": "x"}) == ({"base_url": "x"}, False)
