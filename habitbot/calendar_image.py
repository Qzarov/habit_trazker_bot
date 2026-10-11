"""Календарь месяца картинкой: в какие дни привычка была, а в какие нет."""

from __future__ import annotations

import calendar
import io
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
    year: int,
    month: int,
    marks: set[date],
    created_on: date,
    today: date,
) -> bytes:
    """PNG с сеткой месяца (неделя с понедельника).

    Отмеченные дни несут эмодзи привычки: у полезной это дни «сделано», у вредной — «было».
    """
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    xlim, ylim = (0, 7), (-len(weeks) - 0.75, 1.55)
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
