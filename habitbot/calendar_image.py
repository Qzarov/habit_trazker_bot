"""Календарь месяца картинкой: в какие дни привычка была, а в какие нет."""

from __future__ import annotations

import calendar
import io
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch
from PIL import Image, ImageDraw, ImageFont

#: Шрифты с цветными эмодзи и размер, в котором у них есть картинки (он у таких шрифтов один).
EMOJI_FONTS = [
    ("/System/Library/Fonts/Apple Color Emoji.ttc", 160),
    ("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", 109),
    ("/usr/share/fonts/noto/NotoColorEmoji.ttf", 109),
    ("/usr/share/fonts/google-noto-emoji/NotoColorEmoji.ttf", 109),
]


@lru_cache(maxsize=256)
def emoji_image(emoji: str) -> np.ndarray | None:
    """Эмодзи картинкой (RGBA) или `None`, если нарисовать нечем или не получилось.

    Текстовый шрифт календаря цветных эмодзи не содержит, поэтому глиф берём из системного
    шрифта эмодзи и вклеиваем изображением.
    """
    for path, size in EMOJI_FONTS:
        if not Path(path).exists():
            continue
        try:
            font = ImageFont.truetype(path, size)
            canvas = Image.new("RGBA", (size * 3, size * 2), (0, 0, 0, 0))
            ImageDraw.Draw(canvas).text(
                (size // 4, size // 4), emoji, font=font, embedded_color=True
            )
        except (OSError, ValueError):
            continue
        box = canvas.getbbox()
        if box is None:
            continue
        glyph = canvas.crop(box)
        # Составные эмодзи без поддержки связок рисуются несколькими знаками в ряд — такой
        # результат хуже, чем ничего.
        if glyph.width > glyph.height * 1.35:
            continue
        side = max(glyph.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(glyph, ((side - glyph.width) // 2, (side - glyph.height) // 2))
        return np.asarray(square)
    return None


def can_draw(emoji: str) -> bool:
    return emoji_image(emoji) is not None


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


#: Высота блока показателей под сеткой, в единицах сетки.
STATS_HEIGHT = 1.25


def days_word(count: int) -> str:
    """«день», «дня» или «дней» по числу."""
    if count % 100 in (11, 12, 13, 14):
        return "дней"
    return {1: "день", 2: "дня", 3: "дня", 4: "дня"}.get(count % 10, "дней")


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
    emoji: str | None = None,
    stats: list[tuple[int, str]] | None = None,
    year: int,
    month: int,
    marks: set[date],
    created_on: date,
    today: date,
) -> bytes:
    """PNG с сеткой месяца (неделя с понедельника).

    Отмеченные дни несут эмодзи привычки: у полезной это дни «сделано», у вредной — «было».
    `stats` — показатели под сеткой, пары «число дней, подпись»: у вредной привычки это дни без
    срыва и лучшая серия.
    """
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    xlim, ylim = (0, 7), (-len(weeks) - 0.75 - (STATS_HEIGHT if stats else 0), 1.55)
    # Дюйм на единицу по обеим осям: клетки и эмодзи остаются квадратными.
    fig, ax = plt.subplots(figsize=(7, ylim[1] - ylim[0]), dpi=150)
    ax.set_aspect("equal")
    ax.axis("off")
    glyph = emoji_image(emoji) if emoji else None

    def stamp(cx: float, cy: float, size: float) -> None:
        half = size / 2
        ax.imshow(glyph, extent=(cx - half, cx + half, cy - half, cy + half), zorder=3)

    kind = "вредная привычка" if is_bad else "полезная привычка"
    title_x = 0.05
    if glyph is not None:
        stamp(0.3, 1.2, 0.5)
        title_x = 0.68
    ax.text(title_x, 1.2, name, fontsize=17, fontweight="bold", color=INK, va="center")
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
            if glyph is not None and day in marks and style != "idle":
                # Отмеченный день: эмодзи по центру, число — мелко в углу.
                stamp(col + 0.5, -row - 0.55, 0.5)
                ax.text(
                    col + 0.2, -row - 0.24, str(day.day),
                    fontsize=8, color=ink, ha="left", va="center", fontweight="bold",
                )  # fmt: skip
                continue
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

    if stats:
        # Плашки с показателями: крупное число дней и подпись под ним.
        top = y - 0.45
        width = (7 - 0.16 - 0.2 * (len(stats) - 1)) / len(stats)
        for index, (days, label) in enumerate(stats):
            left = 0.08 + index * (width + 0.2)
            ax.add_patch(
                FancyBboxPatch(
                    (left, top - 0.95),
                    width,
                    0.95,
                    boxstyle="round,pad=0,rounding_size=0.16",
                    facecolor=EMPTY,
                    edgecolor="none",
                )
            )
            ax.text(
                left + width / 2, top - 0.36, f"{days} {days_word(days)}",
                fontsize=20, fontweight="bold", color=INK, ha="center", va="center",
            )  # fmt: skip
            ax.text(
                left + width / 2, top - 0.74, label,
                fontsize=10, color=MUTED, ha="center", va="center",
            )  # fmt: skip

    # imshow сдвигает границы осей — возвращаем их после всех вклеек.
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight", facecolor="white", pad_inches=0.25)
    plt.close(fig)
    return buffer.getvalue()


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


@dataclass(frozen=True)
class OverviewHabit:
    """Привычка для общего календаря."""

    name: str
    emoji: str
    is_bad: bool
    marks: frozenset[date]
    #: Для вредной привычки: дни без срыва сейчас и лучшая серия; у полезной — пусто.
    clean_days: int | None = None
    best_days: int | None = None


#: Сколько значков помещается в клетке дня: три в ряд, два ряда.
CELL_SLOTS = 6


def render_overview(*, year: int, month: int, habits: list[OverviewHabit], today: date) -> bytes:
    """Общий календарь месяца: на каждом дне — значки привычек, отмеченных в этот день.

    Полезная привычка на зелёной плашке (сделано), вредная — на красной (было). Если отмеченных
    в день больше, чем помещается, последняя ячейка показывает «+N». Под сеткой — легенда:
    значок, название и число отмеченных дней за месяц; у вредных привычек второй строкой —
    дни без срыва сейчас и лучшая серия.
    """
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    legend_rows = (len(habits) + 1) // 2
    # Строка легенды выше, если у привычек есть вторая строчка с сериями.
    row_height = 0.6 if any(h.clean_days is not None for h in habits) else 0.42
    bottom = -len(weeks) - 0.35 - row_height * max(legend_rows, 1) - 0.2
    xlim, ylim = (0, 7), (bottom, 1.55)
    fig, ax = plt.subplots(figsize=(7, ylim[1] - ylim[0]), dpi=150)
    ax.set_aspect("equal")
    ax.axis("off")

    def chip(cx: float, cy: float, size: float, habit: OverviewHabit) -> None:
        half = size / 2
        ax.add_patch(
            FancyBboxPatch(
                (cx - half, cy - half),
                size,
                size,
                boxstyle=f"round,pad=0,rounding_size={size * 0.25}",
                facecolor=RED if habit.is_bad else GREEN,
                edgecolor="none",
                zorder=2,
            )
        )
        glyph = emoji_image(habit.emoji)
        if glyph is not None:
            inner = half * 0.74
            ax.imshow(glyph, extent=(cx - inner, cx + inner, cy - inner, cy + inner), zorder=3)

    ax.text(0.05, 1.2, "Все привычки", fontsize=17, fontweight="bold", color=INK, va="center")
    ax.text(0.05, 0.72, f"{MONTHS[month]} {year}", fontsize=11, color=MUTED, va="center")
    for col, label in enumerate(WEEKDAYS):
        ax.text(col + 0.5, 0.2, label, fontsize=10, color=MUTED, ha="center", va="center")

    for row, week in enumerate(weeks):
        for col, day in enumerate(week):
            if day.month != month:
                continue
            ax.add_patch(
                FancyBboxPatch(
                    (col + 0.05, -row - 0.95),
                    0.9,
                    0.9,
                    boxstyle="round,pad=0,rounding_size=0.14",
                    facecolor=EMPTY,
                    edgecolor=INK if day == today else "none",
                    linewidth=1.8,
                )
            )
            ax.text(
                col + 0.14, -row - 0.2, str(day.day),
                fontsize=8, color=MUTED if day > today else INK, ha="left", va="center",
                fontweight="bold",
            )  # fmt: skip
            marked = [h for h in habits if day in h.marks]
            shown = marked if len(marked) <= CELL_SLOTS else marked[: CELL_SLOTS - 1]
            for slot, habit in enumerate(shown):
                chip(col + 0.23 + 0.27 * (slot % 3), -row - 0.46 - 0.27 * (slot // 3), 0.24, habit)
            if len(marked) > CELL_SLOTS:
                slot = CELL_SLOTS - 1
                ax.text(
                    col + 0.23 + 0.27 * (slot % 3), -row - 0.46 - 0.27 * (slot // 3),
                    f"+{len(marked) - len(shown)}",
                    fontsize=7, color=INK, ha="center", va="center", fontweight="bold",
                )  # fmt: skip

    top = -len(weeks) - 0.35
    if not habits:
        ax.text(0.08, top - 0.2, "Привычек пока нет", fontsize=10, color=MUTED, va="center")
    for index, habit in enumerate(habits):
        x = 0.2 + 3.5 * (index % 2)
        y = top - 0.2 - row_height * (index // 2)
        chip(x, y, 0.3, habit)
        days = sum(1 for d in habit.marks if (d.year, d.month) == (year, month))
        name = habit.name if len(habit.name) <= 22 else habit.name[:21] + "…"
        ax.text(x + 0.27, y, f"{name} · {days}", fontsize=10, color=INK, va="center")
        if habit.clean_days is not None and habit.best_days is not None:
            ax.text(
                x + 0.27, y - 0.24,
                f"без срыва {habit.clean_days} {days_word(habit.clean_days)}"
                f" · лучшая серия {habit.best_days}",
                fontsize=8, color=MUTED, va="center",
            )  # fmt: skip

    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight", facecolor="white", pad_inches=0.25)
    plt.close(fig)
    return buffer.getvalue()
