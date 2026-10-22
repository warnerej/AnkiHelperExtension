from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from html import escape
from typing import NamedTuple

from .models import GroupReviews, GroupSummary, YearReviews


@dataclass(frozen=True)
class HeatmapCell:
    day: date
    total_reviews: int = 0
    retention: float | None = None
    study_minutes: float | None = None
    has_data: bool = False
    level: int = 0


def build_grid(profile: YearReviews) -> list[list[HeatmapCell | None]]:
    """Columns are weeks (Monday first), rows are weekdays Mon-Sun.

    Cells outside `profile.start..profile.end` are None. Days the server sent
    no row for are unlit cells without data.
    """
    by_day = {d.study_date: d for d in profile.days}
    first = profile.start - timedelta(days=profile.start.weekday())
    weeks = (profile.end - first).days // 7 + 1 if profile.end >= profile.start else 0
    grid: list[list[HeatmapCell | None]] = []
    for w in range(weeks):
        column: list[HeatmapCell | None] = []
        for wd in range(7):
            day = first + timedelta(days=w * 7 + wd)
            if not profile.start <= day <= profile.end:
                column.append(None)
                continue
            row = by_day.get(day)
            if row is None:
                column.append(HeatmapCell(day))
            else:
                column.append(
                    HeatmapCell(
                        day,
                        row.total_reviews,
                        row.retention,
                        row.study_minutes,
                        has_data=True,
                    )
                )
        grid.append(column)
    return grid


def format_tooltip(cell: HeatmapCell) -> str:
    title = f"{cell.day:%a} {cell.day.day} {cell.day:%b}"
    if not cell.has_data or cell.total_reviews == 0:
        return f"{title}\nNo reviews"
    retention = f"{cell.retention:g}%" if cell.retention is not None else "n/a"
    minutes = f"{cell.study_minutes:g} min" if cell.study_minutes is not None else "n/a"
    return (
        f"{title}\n{cell.total_reviews} reviews\n"
        f"Retention: {retention}\nTime: {minutes}"
    )


# Colour ramp (light to dark) and legend factors follow the Review Heatmap look.
_COLOURS = (
    "#d6e685", "#bddb7a", "#a2cf6e", "#8cc665", "#6cc644",
    "#44a340", "#2f8a38", "#1e6823", "#195c1f", "#144f1a",
)  # fmt: skip
_FACTORS = (0.125, 0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 4)
_STREAK_STEPS = (0, 7, 14, 21, 30)
_PERCENT_STEPS = (0, 25, 50, 60, 70, 80, 85, 90, 95, 99)
VIEW_CMD = "ankihelper:view:"
RETRY_CMD = "ankihelper:retry"
GROUPS_CMD = "ankihelper:groups"
GROUP_CMD = "ankihelper:group:"
_DAY_LETTERS = "MTWTFSS"
_CELL = 10
_PITCH = 12
_LEFT = 18
_TOP = 18


class HeatmapStats(NamedTuple):
    average: int
    learned_percent: int
    longest_streak: int
    current_streak: int


def legend_for(profile: YearReviews) -> list[float]:
    """Level thresholds, scaled by the average over active days (min 20)."""
    active = [d.total_reviews for d in profile.days if d.total_reviews > 0]
    base = max(20, sum(active) / len(active)) if active else 20
    return [f * base for f in _FACTORS]


def level_for(count: int, legend: Sequence[float]) -> int:
    """0 for no reviews, otherwise 1..len(legend)+1."""
    if count <= 0:
        return 0
    return 1 + sum(count >= t for t in legend)


def compute_stats(profile: YearReviews) -> HeatmapStats:
    counts = {d.study_date: d.total_reviews for d in profile.days}
    total_days = (profile.end - profile.start).days + 1
    if total_days <= 0:
        return HeatmapStats(0, 0, 0, 0)
    window = [profile.start + timedelta(days=i) for i in range(total_days)]
    active = [counts.get(d, 0) > 0 for d in window]
    n_active = sum(active)
    average = round(sum(counts.get(d, 0) for d in window) / n_active) if n_active else 0
    longest = run = 0
    for a in active:
        run = run + 1 if a else 0
        longest = max(longest, run)
    # Today may not be studied yet; the streak then runs up to yesterday.
    idx = len(active) - 1
    if idx > 0 and not active[idx]:
        idx -= 1
    current = 0
    while idx >= 0 and active[idx]:
        current += 1
        idx -= 1
    return HeatmapStats(average, round(100 * n_active / total_days), longest, current)


def _step_class(value: float, steps: tuple[int, ...]) -> int:
    return sum(value >= s for s in steps[1:])


def _month_labels(grid: list[list[HeatmapCell | None]]) -> list[tuple[int, str]]:
    labels: list[tuple[int, str]] = []
    for w, column in enumerate(grid):
        days = [c.day for c in column if c is not None]
        if not days:
            continue
        starts_month = any(d.day == 1 for d in days)
        if (w == 0 or starts_month) and (not labels or w - labels[-1][0] >= 2):
            anchor = next((d for d in days if d.day == 1), days[0])
            text = f"{anchor:%b}"
            if anchor.month == 1:
                text += f" '{anchor:%y}"
            labels.append((w, text))
    return labels


def _with_levels(
    grid: list[list[HeatmapCell | None]], legend: list[float]
) -> list[list[HeatmapCell | None]]:
    return [
        [
            None
            if c is None
            else HeatmapCell(
                c.day,
                c.total_reviews,
                c.retention,
                c.study_minutes,
                c.has_data,
                level_for(c.total_reviews, legend),
            )
            for c in column
        ]
        for column in grid
    ]


def _colour_vars() -> str:
    return "".join(
        f".ah-svg rect.ah-cell.ah-q{i + 1}{{fill:{c}}}" for i, c in enumerate(_COLOURS)
    )


_CSS = (
    """
<style>
.ah-group { margin: 1.2em auto 0; text-align: left; display: inline-block; max-width: 100%; }
.ah-bar { margin-bottom: 1em; }
.ah-toggle { padding: 4px 14px; margin-right: 6px; border: 1px solid #888; border-radius: 3px;
  background: transparent; color: inherit; cursor: pointer; }
.ah-toggle.ah-active { background: #44a340; border-color: #44a340; color: #fff; }
.ah-member { margin-bottom: 1.6em; }
.ah-scroll { overflow-x: auto; max-width: 100%; }
.ah-name { font-weight: bold; margin-bottom: 6px; font-size: 14px; }
.ah-svg text { font: 11px sans-serif; fill: #888; }
.ah-svg .ah-cell { fill: #eaeaea; shape-rendering: crispEdges; }
.night_mode .ah-svg .ah-cell { fill: #222; }
.ah-svg .ah-cell:hover { stroke: #000; stroke-width: 1px; }
.night_mode .ah-svg .ah-cell:hover { stroke: #ddd; }
.ah-stats { font-size: 13px; line-height: 1.9; margin-top: 6px; }
.ah-stats b { padding: 1px 5px; margin: 0 14px 0 2px; border-radius: 2px; }
.ah-legend { display: inline-block; vertical-align: middle; margin: 0 6px; }
.ah-legend i { display: inline-block; width: 10px; height: 10px; margin-right: 2px; }
.ah-s0 { background: #eaeaea; } .ah-s1 { background: #d6e685; }
.ah-s2 { background: #8cc665; } .ah-s3 { background: #44a340; }
.ah-s4 { background: #1e6823; color: #fff; } .ah-s5 { background: #144f1a; color: #fff; }
.night_mode .ah-s0 { background: #222; }
#ah-tip { position: fixed; display: none; z-index: 9999; width: 160px; padding: 8px;
  background: #222; color: #fff; font-size: 13px; line-height: 1.5; text-align: center; opacity: 0.9;
  border-radius: 3px; pointer-events: none; transform: translate(-50%, -100%);
  animation: ah-fade 0.4s; }
@keyframes ah-fade { from { opacity: 0; } to { opacity: 0.9; } }
"""
    + _colour_vars()
    + """
</style>
"""
)

_JS = """
<script>
(function () {
  document.querySelectorAll(".ah-scroll").forEach(function (el) {
    el.scrollLeft = el.scrollWidth;
  });
  if (window.ahTipReady) { return; }
  window.ahTipReady = true;
  var tip = document.createElement("div");
  tip.id = "ah-tip";
  document.body.appendChild(tip);
  document.addEventListener("mouseover", function (e) {
    var t = e.target;
    var text = t && t.getAttribute && t.getAttribute("data-tip");
    if (!text) { tip.style.display = "none"; return; }
    var lines = text.split("\\n");
    tip.textContent = "";
    lines.forEach(function (l, i) {
      var el = document.createElement(i === 0 ? "b" : "span");
      el.textContent = l;
      tip.appendChild(el);
      tip.appendChild(document.createElement("br"));
    });
    var r = t.getBoundingClientRect();
    tip.style.left = r.left + r.width / 2 + "px";
    tip.style.top = r.top - 6 + "px";
    tip.style.display = "block";
  });
})();
</script>
"""


def render_grid_html(grid: list[list[HeatmapCell | None]]) -> str:
    """Inline SVG, oldest week first, so the start of the window is on the left."""
    width = _LEFT + len(grid) * _PITCH
    height = _TOP + 7 * _PITCH
    parts = [
        (
            f'<svg class="ah-svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">'
        )
    ]
    for i, letter in enumerate(_DAY_LETTERS):
        y = _TOP + i * _PITCH + _CELL
        parts.append(f'<text x="0" y="{y}">{letter}</text>')
    for w, label in _month_labels(grid):
        parts.append(f'<text x="{_LEFT + w * _PITCH}" y="12">{escape(label)}</text>')
    for w, column in enumerate(grid):
        for r, cell in enumerate(column):
            if cell is None:
                continue
            cls = "ah-cell" + (f" ah-q{min(cell.level, 10)}" if cell.level else "")
            tip = escape(format_tooltip(cell), quote=True)
            parts.append(
                f'<rect class="{cls}" x="{_LEFT + w * _PITCH}" '
                f'y="{_TOP + r * _PITCH}" width="{_CELL}" height="{_CELL}" '
                f'data-tip="{tip}"/>'
            )
    parts.append("</svg>")
    return "".join(parts)


def render_stats_html(stats: HeatmapStats) -> str:
    def val(value: int, steps: tuple[int, ...], suffix: str = "") -> str:
        return f'<b class="ah-s{min(_step_class(value, steps), 5)}">{value}{suffix}</b>'

    legend = "".join(f'<i style="background:{c}"></i>' for c in _COLOURS[::2])
    return (
        '<div class="ah-stats">'
        f"Daily average: {val(stats.average, (0, 20, 40, 80, 160))} "
        f"Days learned: {val(stats.learned_percent, _PERCENT_STEPS, '%')} "
        f"Longest streak: {val(stats.longest_streak, _STREAK_STEPS)} "
        f"Current streak: {val(stats.current_streak, _STREAK_STEPS)}"
        f'<div>Less <span class="ah-legend"><i class="ah-s0"></i>{legend}</span>'
        "More</div></div>"
    )


def _render_profile(name: str, profile: YearReviews, label: str = "") -> str:
    title = escape(label or name)
    grid = _with_levels(build_grid(profile), legend_for(profile))
    body = f'<div class="ah-scroll">{render_grid_html(grid)}</div>' + (
        render_stats_html(compute_stats(profile))
    )
    return f'<div class="ah-member"><div class="ah-name">{title}</div>{body}</div>'


def _picker(groups: Sequence[GroupSummary], selected: str | None) -> str:
    options = "".join(
        f'<option value="{escape(g.id)}"{" selected" if g.id == selected else ""}>'
        f"{escape(g.name)} ({g.member_count})</option>"
        for g in groups
    )
    return (
        '<select class="ah-toggle" '
        f"onchange=\"pycmd('{GROUP_CMD}' + this.value)\">{options}</select>"
    )


def _groups_body(
    groups: Sequence[GroupSummary],
    selected: str | None,
    reviews: GroupReviews | None,
    status: str,
) -> str:
    if status in ("loading", "idle"):
        return (
            '<div class="ah-name">Loading groups...</div>'
            "<div>The server may be waking up; this can take a couple of minutes.</div>"
        )
    if status == "error":
        return (
            '<div class="ah-name">Couldn\'t reach the server.</div>'
            f'<button class="ah-toggle" onclick="pycmd(\'{RETRY_CMD}\')">Retry</button>'
        )
    if not groups:
        return (
            '<div class="ah-name">Create or join a group</div>'
            "<div>Groups let you share your review stats with friends.</div>"
            f'<button class="ah-toggle" onclick="pycmd(\'{GROUPS_CMD}\')">'
            "Open Groups</button>"
        )
    body = f'<div class="ah-name">{_picker(groups, selected)}</div>'
    if reviews is None:
        return body
    if len(reviews.members) <= 1:
        body += (
            "<div>Only you are in this group so far. Open Manage groups to copy "
            "its invite code and allow your friends' usernames.</div>"
        )
    else:
        body += _render_profile("Group total", reviews.total(), label="Group total")
    members = {m.username: reviews.year_for(m) for m in reviews.members}
    return body + "".join(_render_profile(n, p) for n, p in members.items())


def render_heatmap_html(
    mine: YearReviews | None,
    groups: Sequence[GroupSummary],
    selected: str | None,
    reviews: GroupReviews | None,
    view: str,
    status: str = "loaded",
) -> str:
    """Toggle bar plus either the user's own heatmap or the selected group's."""

    def button(target: str, text: str) -> str:
        active = " ah-active" if view == target else ""
        return (
            f'<button class="ah-toggle{active}" '
            f"onclick=\"pycmd('{VIEW_CMD}{target}')\">{text}</button>"
        )

    if view == "groups":
        body = _groups_body(groups, selected, reviews, status)
    elif mine is not None:
        body = _render_profile(mine.username, mine, label="My stats")
    else:
        body = ""
    manage = (
        f'<button class="ah-toggle" onclick="pycmd(\'{GROUPS_CMD}\')">Manage groups</button>'
        if view == "groups"
        else ""
    )
    bar = (
        f'<div class="ah-bar">{button("mine", "My stats")}'
        f"{button('groups', 'Groups')}{manage}</div>"
    )
    return f'{_CSS}<div class="ah-group">{bar}{body}</div>{_JS}'
