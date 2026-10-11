"""Календарь месяца картинкой: в какие дни привычка была, а в какие нет."""

from __future__ import annotations

import calendar
import io
from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

MONTHS = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]  # fmt: skip
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

GREEN = "#34c759"
RED = "#ff3b30"
GREY = "#e5e5ea"
EMPTY = "#f7f7f9"
INK = "#1c1c1e"
MUTED = "#8e8e93"


def day_style(day: date, *, is_bad: bool, marked: bool, created_on: date, today: date) -> str:
    """Что рисовать в клетке: `hit` — хорошо, `miss` — плохо, `idle` — не в счёт.

    Полезная: отмечена → хорошо, прошедший день без отметки → плохо. Вредная наоборот:
    отметка значит «сорвался». Дни до заведения привычки и будущие — не в счёт. Сегодняшний
    день без отметки тоже не в счёт: он ещё не закончился.
    """
    if day < created_on or day > today:
        return "idle"
    if is_bad:
        if marked:
            return "miss"
        return "idle" if day == today else "hit"
    if marked:
        return "hit"
    return "idle" if day == today else "miss"


def render_month(
    *,
    name: str,
    is_bad: bool,
    year: int,
    month: int,
    marks: set[date],
    created_on: date,
    today: date,
) -> bytes:
    """PNG с сеткой месяца (неделя с понедельника)."""
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    fig, ax = plt.subplots(figsize=(7, 1.9 + 0.95 * len(weeks)), dpi=150)
    ax.set_xlim(0, 7)
    ax.set_ylim(-len(weeks) - 0.75, 1.55)
    ax.axis("off")

    kind = "вредная привычка" if is_bad else "полезная привычка"
    ax.text(0.05, 1.2, name, fontsize=17, fontweight="bold", color=INK, va="center")
    ax.text(0.05, 0.72, f"{MONTHS[month]} {year} · {kind}", fontsize=11, color=MUTED, va="center")
    for col, label in enumerate(WEEKDAYS):
        ax.text(col + 0.5, 0.2, label, fontsize=10, color=MUTED, ha="center", va="center")

    colors = {
        "hit": (GREEN, "white"),
        "miss": (RED, "white") if is_bad else (GREY, INK),
        "idle": (EMPTY, MUTED),
    }
    for row, week in enumerate(weeks):
        for col, day in enumerate(week):
            if day.month != month:
                continue
            style = day_style(
                day, is_bad=is_bad, marked=day in marks, created_on=created_on, today=today
            )
            fill, ink = colors[style]
            ax.add_patch(
                FancyBboxPatch(
                    (col + 0.08, -row - 0.92),
                    0.84,
                    0.84,
                    boxstyle="round,pad=0,rounding_size=0.16",
                    facecolor=fill,
                    edgecolor=INK if day == today else "none",
                    linewidth=1.8,
                )
            )
            ax.text(
                col + 0.5, -row - 0.5, str(day.day),
                fontsize=13, color=ink, ha="center", va="center",
                fontweight="bold" if style != "idle" else "normal",
            )  # fmt: skip

    legend = (
        [(GREEN, "не было"), (RED, "было")] if is_bad else [(GREEN, "сделано"), (GREY, "пропуск")]
    )
    y = -len(weeks) - 0.4
    x = 0.08
    for fill, label in legend:
        ax.add_patch(
            FancyBboxPatch(
                (x, y - 0.13),
                0.26,
                0.26,
                boxstyle="round,pad=0,rounding_size=0.06",
                facecolor=fill,
                edgecolor="none",
            )
        )
        ax.text(x + 0.36, y, label, fontsize=10, color=MUTED, va="center")
        x += 1.6
    ax.text(6.92, y, "обведён сегодняшний день", fontsize=9, color=MUTED, va="center", ha="right")

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight", facecolor="white", pad_inches=0.25)
    plt.close(fig)
    return buffer.getvalue()


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1
