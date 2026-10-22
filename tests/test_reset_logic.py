import pytest

from addon.reset_logic import validate_reset_email


@pytest.mark.parametrize("email", ["a@b.c", " a@b.co ", "first.last+tag@example.com"])
def test_valid_emails(email):
    assert validate_reset_email(email) is None


@pytest.mark.parametrize("email", ["", "   ", "nope", "a@b", "a b@c.d", "a@@b.c"])
def test_invalid_emails(email):
    assert validate_reset_email(email)
