from datetime import date

from addon.heatmap import (
    HeatmapCell,
    build_grid,
    compute_stats,
    format_tooltip,
    legend_for,
    level_for,
    render_friends_html,
)
from addon.models import FriendDay, ProfileReviews


def profile(days: list[FriendDay]) -> ProfileReviews:
    # Mon 2026-09-14 .. Tue 2026-10-13 (30 days)
    return ProfileReviews(
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
    p = ProfileReviews(username="a", start=date(2026, 9, 16), end=date(2026, 9, 20))
    grid = build_grid(p)
    assert grid[0][0] is None and grid[0][1] is None
    assert grid[0][2] is not None and grid[0][2].day == date(2026, 9, 16)


def test_lit_only_with_reviews():
    days = [
        FriendDay(study_date=date(2026, 9, 14), total_reviews=5),
        FriendDay(study_date=date(2026, 9, 15), total_reviews=0),
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
    days = [FriendDay(study_date=date(2026, 9, 14), total_reviews=5, retention=90)]
    html = render_friends_html({"bob": profile(days), "ghost": None})
    assert html.index(">bob<") < html.index("ghost (user not found)")
    first = html.index("Mon 14 Sep")
    last = html.index("Tue 13 Oct")
    assert first < last
    assert "ah-q1" in html
    assert "5 reviews" in html
    assert html.count("<style>") == 1


def test_svg_labels_and_colours():
    html = render_friends_html({"bob": profile([])})
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
        FriendDay(study_date=date(2026, 9, 14), total_reviews=10),
        FriendDay(study_date=date(2026, 9, 15), total_reviews=30),
        FriendDay(study_date=date(2026, 10, 12), total_reviews=20),
    ]
    stats = compute_stats(profile(days))
    assert stats.average == 20
    assert stats.learned_percent == 10
    assert stats.longest_streak == 2
    assert stats.current_streak == 1  # today empty, yesterday active
    assert compute_stats(profile([])) == (0, 0, 0, 0)


def test_html_escapes_and_empty():
    assert render_friends_html({}) == ""
    p = ProfileReviews(username="x", start=date(2026, 9, 14), end=date(2026, 9, 14))
    assert "<b>" not in render_friends_html({"<b>": p})


def test_year_window():
    # Mon 2025-10-20 .. Sat 2026-10-17 (363 days), January label carries the year
    p = ProfileReviews(
        username="bob",
        start=date(2025, 10, 20),
        end=date(2026, 10, 17),
        days=[FriendDay(study_date=date(2026, 10, 17), total_reviews=30)],
    )
    grid = build_grid(p)
    assert len(grid) == 52
    html = render_friends_html({"bob": p})
    assert "Jan &#x27;26" in html or "Jan '26" in html
    assert 'class="ah-scroll"' in html
    assert compute_stats(p).learned_percent == 0
