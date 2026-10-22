import logging

import pytest

from addon.log import redact, setup_logging

from .conftest import tokens


@pytest.mark.parametrize(
    "raw,secret",
    [
        ("Authorization: Bearer abc.def-123", "abc.def-123"),
        ('{"access_token": "tok123", "x": 1}', "tok123"),
        ("refresh_token=rt456&x=1", "rt456"),
        ("password='hunter2'", "hunter2"),
    ],
)
def test_redact(raw, secret):
    assert secret not in redact(raw)


def test_logs_never_contain_secrets(tmp_path, backend, make_client):
    setup_logging(tmp_path, debug=True)
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("GET", "/auth/me", (200, {"id": "u", "email": "a@b.c"}))
    backend.on("POST", "/auth/logout", (200, {}))
    client = make_client()
    client.get_me()
    logging.getLogger("ankihelper").info(
        "oops Bearer access1 password=pw-secret", exc_info=None
    )
    client.close()
    for handler in logging.getLogger("ankihelper").handlers:
        handler.flush()
    text = (tmp_path / "ankihelper.log").read_text()
    assert "GET /auth/me -> 200" in text
    for secret in ("access1", "refresh1", "pw-secret"):
        assert secret not in text


def test_stack_info_is_scrubbed_and_the_record_is_not_mutated(tmp_path):
    from addon.log import RedactingFormatter

    record = logging.LogRecord(
        "ankihelper", logging.INFO, __file__, 1, "token=%s", ("t",), None
    )
    record.stack_info = "frame: Authorization: Bearer leaky.token"
    line = RedactingFormatter("%(message)s").format(record)
    assert "leaky.token" not in line
    assert record.stack_info == "frame: Authorization: Bearer leaky.token"
    assert record.args == ("t",)
