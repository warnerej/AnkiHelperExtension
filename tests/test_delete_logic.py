from addon.delete_logic import validate_delete

EMAIL = "a@b.c"


def test_valid_when_email_matches_ignoring_case_and_spaces():
    assert validate_delete(EMAIL, "  A@B.C ", "pw") is None


def test_email_mismatch():
    assert "does not match" in (validate_delete(EMAIL, "x@y.z", "pw") or "")


def test_empty_password():
    assert validate_delete(EMAIL, EMAIL, "") == "Enter your password."


def test_unknown_expected_email_blocks_deletion():
    assert "look up" in (validate_delete(None, "", "pw") or "")
    assert "look up" in (validate_delete("", "", "pw") or "")
