from dataclasses import dataclass
from datetime import date, timedelta
from html import escape

from .models import ProfileReviews


@dataclass(frozen=True)
class HeatmapCell:
    day: date
    total_reviews: int = 0
    retention: float | None = None
    study_minutes: float | None = None
    has_data: bool = False

    @property
    def lit(self) -> bool:
        return self.total_reviews > 0


def build_grid(profile: ProfileReviews) -> list[list[HeatmapCell | None]]:
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


_CSS = """
<style>
.ah-friends { margin: 1.2em auto 0; text-align: left; display: inline-block; }
.ah-friend { margin-bottom: 1em; }
.ah-name { font-weight: bold; margin-bottom: 4px; }
.ah-grid { display: grid; grid-auto-flow: column; grid-template-rows: repeat(7, 14px);
  grid-auto-columns: 14px; gap: 3px; }
.ah-cell { border-radius: 3px; background: rgba(128, 128, 128, 0.25); }
.ah-cell.lit { background: #39a96b; }
.ah-cell.pad { background: none; }
</style>
"""


def render_grid_html(grid: list[list[HeatmapCell | None]]) -> str:
    """Oldest week first, so the start of the window is on the left."""
    cells: list[str] = []
    for column in grid:
        for cell in column:
            if cell is None:
                cells.append('<div class="ah-cell pad"></div>')
            else:
                cls = "ah-cell lit" if cell.lit else "ah-cell"
                tip = escape(format_tooltip(cell), quote=True)
                cells.append(f'<div class="{cls}" title="{tip}"></div>')
    return f'<div class="ah-grid">{"".join(cells)}</div>'


def render_friends_html(friends: dict[str, ProfileReviews | None]) -> str:
    """HTML block for the deck browser; empty when no friends are configured."""
    if not friends:
        return ""
    blocks: list[str] = []
    for name, profile in friends.items():
        if profile is None:
            title = f"{escape(name)} (user not found)"
            body = ""
        else:
            title = escape(name)
            body = render_grid_html(build_grid(profile))
        blocks.append(
            f'<div class="ah-friend"><div class="ah-name">{title}</div>{body}</div>'
        )
    return f'{_CSS}<div class="ah-friends">{"".join(blocks)}</div>'
