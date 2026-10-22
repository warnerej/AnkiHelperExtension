from datetime import date

from addon.heatmap import (
    HeatmapCell,
    build_grid,
    compute_stats,
    format_tooltip,
    legend_for,
    level_for,
    render_heatmap_html,
    render_members_html,
)
from addon.models import (
    GroupReviews,
    GroupSummary,
    MemberReviews,
    ReviewDay,
    YearReviews,
)


def profile(days: list[ReviewDay]) -> YearReviews:
    # Mon 2026-09-14 .. Tue 2026-10-13 (30 days)
    return YearReviews(
        username="bob", start=date(2026, 9, 14), end=date(2026, 10, 13), days=days
    )


def test_grid_shape_and_padding():
    grid = build_grid(profile([]))
    assert len(grid) == 5
    assert all(len(col) == 7 for col in grid)
    assert grid[0][0] is not None and grid[0][0].day == date(2026, 9, 14)
    assert grid[-1][0] is not None and grid[-1][0].day == date(2026, 10, 12)
    assert grid[-1][1] is not None and grid[-1][1].day == date(2026, 10, 13)
    assert grid[-1][2] is None


def test_padding_before_start():
    p = YearReviews(username="a", start=date(2026, 9, 16), end=date(2026, 9, 20))
    grid = build_grid(p)
    assert grid[0][0] is None and grid[0][1] is None
    assert grid[0][2] is not None and grid[0][2].day == date(2026, 9, 16)


def test_lit_only_with_reviews():
    days = [
        ReviewDay(study_date=date(2026, 9, 14), total_reviews=5),
        ReviewDay(study_date=date(2026, 9, 15), total_reviews=0),
    ]
    grid = build_grid(profile(days))
    assert grid[0][0] is not None and grid[0][0].lit
    assert grid[0][1] is not None and not grid[0][1].lit
    assert grid[0][2] is not None and not grid[0][2].lit


def test_tooltip():
    cell = HeatmapCell(date(2026, 10, 5), 42, 91.0, 18.5, has_data=True)
    assert format_tooltip(cell) == (
        "Mon 5 Oct\n42 reviews\nRetention: 91%\nTime: 18.5 min"
    )


def test_tooltip_missing_values_and_no_reviews():
    cell = HeatmapCell(date(2026, 10, 5), 3, None, None, has_data=True)
    assert "Retention: n/a" in format_tooltip(cell)
    assert "Time: n/a" in format_tooltip(cell)
    assert format_tooltip(HeatmapCell(date(2026, 10, 5))).endswith("No reviews")


def test_html_is_chronological_with_name_and_tooltips():
    days = [ReviewDay(study_date=date(2026, 9, 14), total_reviews=5, retention=90)]
    html = render_members_html({"bob": profile(days), "ghost": None})
    assert html.index(">bob<") < html.index("ghost (user not found)")
    first = html.index("Mon 14 Sep")
    last = html.index("Tue 13 Oct")
    assert first < last
    assert "ah-q1" in html
    assert "5 reviews" in html
    assert html.count("<style>") == 1


def test_svg_labels_and_colours():
    html = render_members_html({"bob": profile([])})
    assert html.count('x="0" y=') == 7
    assert ">Sep<" in html and ">Oct<" in html
    assert "ah-today" not in html
    assert "rect.ah-cell.ah-q1{fill:" in html


def test_level_for():
    legend = [1, 2, 4]
    assert level_for(0, legend) == 0
    assert level_for(1, legend) == 2
    assert level_for(3, legend) == 3
    assert level_for(99, legend) == 4
    assert len(legend_for(profile([]))) == 9


def test_compute_stats():
    days = [
        ReviewDay(study_date=date(2026, 9, 14), total_reviews=10),
        ReviewDay(study_date=date(2026, 9, 15), total_reviews=30),
        ReviewDay(study_date=date(2026, 10, 12), total_reviews=20),
    ]
    stats = compute_stats(profile(days))
    assert stats.average == 20
    assert stats.learned_percent == 10
    assert stats.longest_streak == 2
    assert stats.current_streak == 1  # today empty, yesterday active
    assert compute_stats(profile([])) == (0, 0, 0, 0)


def test_html_escapes_and_empty():
    assert render_members_html({}) == ""
    p = YearReviews(username="x", start=date(2026, 9, 14), end=date(2026, 9, 14))
    assert "<b>" not in render_members_html({"<b>": p})


def test_year_window():
    # Mon 2025-10-20 .. Sat 2026-10-17 (363 days), January label carries the year
    p = YearReviews(
        username="bob",
        start=date(2025, 10, 20),
        end=date(2026, 10, 17),
        days=[ReviewDay(study_date=date(2026, 10, 17), total_reviews=30)],
    )
    grid = build_grid(p)
    assert len(grid) == 52
    html = render_members_html({"bob": p})
    assert "Jan &#x27;26" in html or "Jan '26" in html
    assert 'class="ah-scroll"' in html
    assert compute_stats(p).learned_percent == 0


def group(*members: tuple[str, int]) -> GroupReviews:
    return GroupReviews(
        "g1",
        date(2026, 1, 1),
        date(2026, 1, 31),
        [
            MemberReviews(
                name, [ReviewDay(study_date=date(2026, 1, 5), total_reviews=n)]
            )
            for name, n in members
        ],
    )


G1 = GroupSummary("g1", "Crew <1>", "owner", 2)
G2 = GroupSummary("g2", "Other", "member", 1)


def test_heatmap_toggle_views():
    mine = profile([ReviewDay(study_date=date(2026, 1, 5), total_reviews=3)])
    own = render_heatmap_html(mine, [G1], "g1", group(("bob", 1), ("al", 2)), "mine")
    assert "My stats" in own and "bob</div>" not in own
    assert "ankihelper:view:groups" in own
    other = render_heatmap_html(
        mine, [G1], "g1", group(("bob", 1), ("al", 2)), "groups"
    )
    assert "bob</div>" in other and "al</div>" in other and "ah-friend" in other
    assert "Group total" in other
    assert "ah-toggle" in render_heatmap_html(None, [], None, None, "mine")


def test_group_picker_marks_selection_and_escapes():
    html = render_heatmap_html(None, [G1, G2], "g2", group(("a", 1)), "groups")
    assert 'value="g2" selected' in html and 'value="g1" selected' not in html
    assert "Crew &lt;1&gt; (2)" in html and "Crew <1>" not in html
    assert "ankihelper:group:" in html


def test_manage_groups_button_only_on_groups_view():
    assert "ankihelper:groups" not in render_heatmap_html(None, [], None, None, "mine")
    assert "ankihelper:groups" in render_heatmap_html(None, [], None, None, "groups")


def test_empty_state_when_no_groups():
    html = render_heatmap_html(None, [], None, None, "groups")
    assert "Create or join a group" in html and "Open Groups" in html


def test_invite_hint_when_only_user_in_group():
    html = render_heatmap_html(None, [G1], "g1", group(("me", 4)), "groups")
    assert "Only you are in this group" in html
    assert "Group total" not in html and "me</div>" in html


def test_heatmap_groups_status_states():
    loading = render_heatmap_html(None, [], None, None, "groups", "loading")
    assert "Loading groups" in loading
    error = render_heatmap_html(None, [], None, None, "groups", "error")
    assert "Retry" in error
    loaded = render_heatmap_html(
        None, [G1], "g1", group(("bob", 1), ("al", 1)), "groups"
    )
    assert "ah-friend" in loaded
