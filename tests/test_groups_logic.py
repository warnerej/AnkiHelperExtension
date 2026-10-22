from datetime import UTC, datetime

from addon import groups_logic as gl
from addon.errors import ApiError, NetworkError
from addon.models import Group, GroupMember

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_group(*, owner_view: bool) -> Group:
    return Group(
        id="g1",
        name="Study Crew",
        owner="alice",
        members=[
            GroupMember("alice", "owner", NOW),
            GroupMember("bob", "member", NOW),
        ],
        invite_code="CODE" if owner_view else None,
        allowed=["bob", "carol"] if owner_view else None,
    )


def test_owner_controls():
    c = gl.detail_controls(make_group(owner_view=True))
    assert not c.leave
    assert c.invite_code


def test_role_from_the_list_wins_over_the_invite_code_heuristic():
    owner = gl.detail_controls(make_group(owner_view=False), "owner")
    assert owner.invite_code and not owner.leave
    member = gl.detail_controls(make_group(owner_view=True), "member")
    assert member.leave and not member.invite_code


def test_member_controls():
    c = gl.detail_controls(make_group(owner_view=False))
    assert c.leave
    assert not c.invite_code


def test_group_name_validation():
    assert gl.validate_group_name("  My   group ") == ("My group", None)
    assert gl.validate_group_name("   ")[1]
    assert gl.validate_group_name("x" * 51)[1]
    assert gl.validate_group_name("x" * 50)[1] is None


def test_invite_code_validation():
    assert gl.validate_invite_code(" AB CD\n") == ("ABCD", None)
    assert gl.validate_invite_code("  ")[1]


def test_username_validation():
    assert gl.validate_username(" Bob_1 ") == ("bob_1", None)
    assert gl.validate_username("ab")[1]
    assert gl.validate_username("has space")[1]
    assert gl.validate_username("x" * 21)[1]


def test_transfer_candidates_exclude_owner():
    assert gl.transfer_candidates(make_group(owner_view=True)) == ["bob"]


def test_cannot_remove_owner_from_allowed():
    group = make_group(owner_view=True)
    assert not gl.can_remove_allowed(group, "alice")
    assert gl.can_remove_allowed(group, "bob")


def test_remove_confirmation_mentions_membership_only_when_joined():
    group = make_group(owner_view=True)
    assert "also be removed" in gl.remove_allowed_confirmation(group, "bob")
    assert "also be removed" not in gl.remove_allowed_confirmation(group, "carol")


def test_delete_requires_exact_name():
    group = make_group(owner_view=True)
    assert gl.delete_confirmed(group, " Study Crew ")
    assert not gl.delete_confirmed(group, "study crew")
    assert not gl.delete_confirmed(group, "")


def test_join_confirmation_wording():
    assert "last year of review stats" in gl.JOIN_CONFIRMATION


def test_error_messages():
    assert gl.error_message(ApiError(409, "Too many")) == "Too many"
    assert gl.error_message(NetworkError("offline")) == "offline"
    assert gl.STALE_HINT in gl.error_message(NetworkError("offline"), mutation=True)
    assert gl.STALE_HINT not in gl.error_message(ApiError(409, "x"), mutation=True)
    assert "Unexpected" in gl.error_message(ValueError("boom"))


def test_needs_refresh_only_after_network_error():
    assert gl.needs_refresh(NetworkError("x"))
    assert not gl.needs_refresh(ApiError(404, "x"))
