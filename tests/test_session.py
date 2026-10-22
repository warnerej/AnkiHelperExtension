import httpx
import pytest

from addon.errors import ApiError
from addon.session import check_response, format_detail


def test_format_detail_string_passthrough():
    assert format_detail("nope") == "nope"


def test_format_detail_validation_list():
    detail = [
        {
            "loc": ["body", "username"],
            "msg": "String should match pattern",
            "type": "x",
        },
        {"loc": ["body"], "msg": "Field required", "type": "y"},
    ]
    assert (
        format_detail(detail) == "username: String should match pattern; Field required"
    )


def test_format_detail_missing():
    assert format_detail(None) == ""


def test_422_list_detail_is_readable():
    resp = httpx.Response(
        422, json={"detail": [{"loc": ["body", "email"], "msg": "bad email"}]}
    )
    with pytest.raises(ApiError) as info:
        check_response(resp)
    assert info.value.detail == "email: bad email"
