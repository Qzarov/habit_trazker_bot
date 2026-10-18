"""Мотивационные сообщения к напоминаниям: стиль под человека, текст от языковой модели.

Рамка — теория самодетерминации: чем ближе мотивация к «хочу сам», тем дольше держится
привычка. Человек выбирает, что ему ближе (цифры, серии, поддержка, смысл…), бот чаще пишет в
этом стиле, а иногда пробует другие и смотрит, после каких сообщений привычку отмечают.
Тон двигается кнопками «Мягче» и «Жёстче» под сообщением. Стыда и вины нет ни в каком тоне:
они работают пару раз, а потом человек отключает уведомления.

Текст пишет модель через OpenRouter. Если ключа нет, запрос не удался или ответ пустой, берётся
готовая фраза того же стиля из `PRESETS` с цифрами привычки.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import date, timedelta

import aiohttp

from .db import Habit, Motivation, Storage, best_clean_streak, streak

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "~anthropic/claude-sonnet-latest"
TIMEOUT = 25  # секунд: напоминание без мотивации лучше, чем напоминание с опозданием
MAX_CHARS = 400


@dataclass(frozen=True)
class Style:
    label: str  # подпись кнопки
    title: str  # как стиль называется человеку
    brief: str  # что писать — для модели


STYLES: dict[str, Style] = {
    "progress": Style(
        "📈 Цифры",
        "цифры и прогресс",
        "Видимый рост и компетентность: конкретные числа из контекста, сравнение с тем, что "
        "было раньше («неделю назад было 2 из 7, сейчас 5»).",
    ),
    "streak": Style(
        "🔥 Серии",
        "серии",
        "Серия дней и желание её продолжить. Если серия оборвалась — правило «не пропускай "
        "дважды»: главное вернуться сегодня, а не начинать всё заново.",
    ),
    "support": Style(
        "🤝 Поддержка",
        "поддержка",
        "Тёплая поддержка, как от друга: признать усилия, сказать, что пропуски бывают у всех.",
    ),
    "meaning": Style(
        "🧭 Смысл",
        "смысл и ценности",
        "Зачем человеку эта привычка. Опирайся на его собственные слова о том, зачем ему это, "
        "если они есть; не придумывай за него.",
    ),
    "identity": Style(
        "🪞 Идентичность",
        "идентичность",
        "«Я такой человек»: каждое выполнение — ещё одно подтверждение того, кем человек "
        "становится («ты уже человек, который…»).",
    ),
    "curiosity": Style(
        "✨ Интерес",
        "интерес и новизна",
        "Любопытство и удовольствие от самого дела: неожиданный факт, мини-челлендж, вопрос, "
        "который хочется проверить на себе.",
    ),
    "ease": Style(
        "🪶 Малый шаг",
        "лёгкость",
        "Упростить, а не вдохновить: предложи самый маленький шаг, который невозможно не "
        "сделать («хотя бы одно отжимание», «одна страница»).",
    ),
    "autonomy": Style(
        "🎛 Выбор",
        "выбор",
        "Дай выбор вместо приказа: два-три варианта, как сделать сегодня («5 минут или полные "
        "20?»). Решает человек.",
    ),
}

#: Пояснения к стилям для человека: что это и пример сообщения.
ABOUT: dict[str, tuple[str, str]] = {
    "progress": ("видимый рост, сравнение с тем, что было", "Неделю назад было 2 из 7, сейчас 5"),
    "streak": ("не оборвать серию дней подряд", "Серия 12 дней — добавим тринадцатый?"),
    "support": ("тёплые слова, как от друга", "Пропуски бывают у всех, главное — вернуться"),
    "meaning": (
        "ради чего ты всё это начал, твоими словами из «Зачем мне это»",
        "Ты начал ради спины без боли — сегодня ещё шаг туда",
    ),
    "identity": ("«я такой человек»", "Ты уже человек, который бегает по утрам"),
    "curiosity": (
        "новизна, мини-челленджи, любопытство",
        "Попробуй сегодня новое упражнение — что изменится?",
    ),
}

#: Стили, которые предлагают выбрать в настройках; остальные бот пробует сам.
OFFERED = ("progress", "streak", "support", "meaning", "identity", "curiosity")
AUTO = "auto"

TONES = {
    -2: "очень мягко и бережно, без требований",
    -1: "мягко и тепло",
    0: "дружелюбно и по делу",
    1: "бодро и прямо, с лёгким вызовом",
    2: "жёстко и прямо, как тренер: коротко, с напором, но без стыда и оскорблений",
}

#: Доля сообщений в выбранном стиле; остальные — эксперименты с другими стилями.
PREFERRED_SHARE = 0.7


def stage(habit: Habit, today: date) -> tuple[str, tuple[str, ...]]:
    """Этап привычки и стили, которые на нём работают лучше.

    В начале важнее лёгкость и быстрые победы, к 2–4 неделе — прогресс и серии, дальше —
    идентичность и смысл.
    """
    days = (today - habit.created_on).days
    if days < 14:
        return "начало (первые две недели)", ("ease", "support", "autonomy")
    if days < 35:
        return "закрепление (2–5 неделя)", ("progress", "streak")
    return "привычка уже держится (больше месяца)", ("identity", "meaning", "curiosity")


def choose_style(
    motivation: Motivation,
    stats: dict[str, tuple[int, int, int]],
    good_for_stage: tuple[str, ...],
    rng: random.Random,
) -> tuple[str, bool]:
    """Стиль сообщения и пометка «эксперимент».

    В выбранном человеком стиле — `PREFERRED_SHARE` сообщений. Остальные и все при «подбирай
    сам» — по весам: доля сообщений, после которых привычку отметили (с осторожной оценкой для
    редких стилей), плюс бонус за «принято» и за то, что стиль подходит этапу привычки.
    """
    preferred = motivation.style if motivation.style in STYLES else None
    if preferred and rng.random() < PREFERRED_SHARE:
        return preferred, False
    candidates = [s for s in STYLES if s != preferred]
    weights = []
    for name in candidates:
        sent, acted, liked = stats.get(name, (0, 0, 0))
        weight = (acted + 1) / (sent + 2) + 0.1 * liked / (sent + 1)
        if name in good_for_stage:
            weight += 0.3
        weights.append(weight)
    return rng.choices(candidates, weights=weights)[0], preferred is not None


SYSTEM = """\
Ты пишешь короткие мотивирующие сообщения для Telegram-бота, который помогает держать привычки.
Сообщение приходит вместе с напоминанием, под ним кнопки ответа, поэтому не проси нажать кнопку \
и не повторяй вопрос напоминания.

Правила:
- 1–3 предложения, не длиннее 300 символов, по-русски, на «ты».
- Без заголовков, кавычек вокруг текста, хэштегов и списков; эмодзи — не больше одного.
- Пиши про эту конкретную привычку, опирайся на цифры из контекста. Не выдумывай фактов о \
человеке и его жизни.
- Никакой статистики про других людей, исследований и процентов: только цифры из контекста.
- Никакого стыда, вины и давления («ты же обещал», «стыдно», «опять»): даже в жёстком тоне — \
энергия и вызов, а не упрёк.
- После пропуска или срыва — поддержка и самый лёгкий следующий шаг: пропуски бывают у всех, \
главное вернуться.
- Мотивируй стремлением к желаемому, а не страхом.
- Для вредной привычки цель — чтобы её не было: поддерживай «держаться», а не «сделать».
- Не повторяй недавние сообщения ни по смыслу, ни по формулировкам.

Ответь только текстом сообщения."""


def _day_mark(habit: Habit, answer: bool | None) -> str:
    if answer is None:
        return "нет данных"
    if habit.is_bad:
        return "срыв" if answer else "держался"
    return "сделал" if answer else "не сделал"


def build_messages(
    habit: Habit,
    day: date,
    today: date,
    motivation: Motivation,
    style: str,
    answers: dict[date, bool],
    recent: list[str],
) -> list[dict[str, str]]:
    """Запрос к модели: правила в системном сообщении, факты о привычке — в пользовательском."""
    marks = {d for d, value in answers.items() if value}
    stage_name, _ = stage(habit, today)
    week = [day - timedelta(days=i) for i in range(7, 0, -1)]
    history = ", ".join(
        f"{d:%d.%m} {_day_mark(habit, answers.get(d))}" for d in week if d >= habit.created_on
    )
    lines = [
        f"Привычка: «{habit.name}» ({'вредная — цель, чтобы её не было' if habit.is_bad else 'полезная'}).",
        f"Ведёт её {(today - habit.created_on).days + 1} дн., этап: {stage_name}.",
    ]
    if day != today:
        lines.append(f"Напоминание спрашивает про вчерашний день ({day:%d.%m}), он уже прошёл.")
    if habit.is_bad:
        lines.append(
            f"Без срыва сейчас: {streak(habit, marks, today)} дн., "
            f"лучшая серия: {best_clean_streak(habit, marks, today)} дн."
        )
    else:
        lines.append(f"Серия: {streak(habit, marks, today)} дн. подряд.")
    if history:
        lines.append(f"Предыдущие дни: {history}.")
    previous = answers.get(day - timedelta(days=1))
    missed = previous is not None and previous == habit.is_bad
    if missed:
        lines.append("Вчера был пропуск или срыв — нужна поддержка и лёгкий шаг, без упрёков.")
    if motivation.why:
        lines.append(f"Зачем ему это, его словами: «{motivation.why}».")
    lines += [
        f"Стиль мотивации: {STYLES[style].title}. {STYLES[style].brief}",
        f"Тон: {TONES[motivation.tone]}.",
    ]
    if recent:
        lines.append("Недавние сообщения, не повторяй их:\n" + "\n".join(f"- {t}" for t in recent))
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


#: Готовые фразы на случай, когда модель недоступна: по стилю, отдельно для полезной (`good`)
#: и вредной (`bad`) привычки. Подстановки: {name} — название, {streak} — серия (у вредной —
#: дни без срыва), {best} — лучшая серия без срыва, {week} — удачных дней из последних семи.
PRESETS: dict[str, dict[str, tuple[str, ...]]] = {
    "progress": {
        "good": (
            "«{name}»: {week} из последних 7 дней уже в копилке. Сегодня можно сделать {week_next}.",
            "Каждый отмеченный день — +1 к статистике «{name}». Серия сейчас {streak} дн.",
        ),
        "bad": (
            "«{name}»: {week} спокойных дней из последних семи. Сейчас {streak}, рекорд {best}.",
            "Без «{name}» уже {streak} дн. Ещё день — и цифра станет больше.",
        ),
    },
    "streak": {
        "good": (
            "Серия «{name}» — {streak} дн. Добавим ещё один?",
            "Правило серий — не пропускать дважды. Сегодня отличный день для «{name}».",
        ),
        "bad": (
            "{streak} дн. без «{name}». Лучшая серия — {best} дн., есть куда расти.",
            "Серия держится, пока держишься ты: {streak} дн. без «{name}».",
        ),
    },
    "support": {
        "good": (
            "Даже маленький шаг в «{name}» сегодня считается. Ты справляешься.",
            "Какой бы ни был день, на «{name}» найдётся пара минут. Я за тебя.",
        ),
        "bad": (
            "Держаться от «{name}» непросто, и ты это делаешь. Это уже много.",
            "Если тянет к «{name}» — это нормально. Переждать пару минут часто достаточно.",
        ),
    },
    "meaning": {
        "good": (
            "Вспомни, ради чего ты начал «{name}». Сегодня — ещё один шаг туда.",
            "«{name}» — не ради галочки, а ради того, что для тебя важно.",
        ),
        "bad": (
            "Ты отказываешься от «{name}» ради чего-то важного для себя. Помни об этом сегодня.",
            "Каждый день без «{name}» — шаг к тому, ради чего ты это затеял.",
        ),
    },
    "identity": {
        "good": (
            "Ты уже человек, у которого есть «{name}». Сегодня — ещё одно подтверждение.",
            "{streak} дн. подряд — это уже не случайность, это ты.",
        ),
        "bad": (
            "Ты человек, который справляется без «{name}». {streak} дн. это доказывают.",
            "Ты больше не тот, кому нужно «{name}». Просто продолжай быть собой.",
        ),
    },
    "curiosity": {
        "good": (
            "Попробуй сегодня сделать «{name}» чуть по-новому — и заметь, что изменится.",
            "Маленький эксперимент: «{name}» в другое время дня. Как ощущения?",
        ),
        "bad": (
            "Интересно, чем заменить «{name}» сегодня? Попробуй что-нибудь новое.",
            "Заметь, в какие моменты тянет к «{name}». Сегодня можно просто понаблюдать.",
        ),
    },
    "ease": {
        "good": (
            "Самый маленький шаг в «{name}» — уже победа. Хватит и минуты.",
            "Не обязательно на полную: сделай «{name}» хотя бы чуть-чуть.",
        ),
        "bad": (
            "Только сегодня, только этот вечер — без «{name}». Дальше посмотрим.",
            "Не надо думать про «навсегда». Один день без «{name}» — этого достаточно.",
        ),
    },
    "autonomy": {
        "good": (
            "«{name}» сегодня: быстро и по минимуму или как следует? Решать тебе.",
            "Как сегодня удобнее сделать «{name}» — утром, днём или вечером?",
        ),
        "bad": (
            "Чем сегодня заменишь «{name}»? Выбор за тобой.",
            "Ты решаешь, каким будет этот вечер. Можно и без «{name}».",
        ),
    },
}

#: После пропуска или срыва — поддержка вместо стиля.
AFTER_MISS: dict[str, tuple[str, ...]] = {
    "good": (
        "Пропуски бывают у всех. Главное — не пропускать дважды: вернуть «{name}» можно сегодня.",
        "Вчера не вышло — ничего страшного. Сегодня хватит самого маленького шага в «{name}».",
    ),
    "bad": (
        "Срывы бывают у всех, это не перечёркивает пройденное. Сегодня — новый день без «{name}».",
        "Вчера было непросто. Начнём сначала: один спокойный день без «{name}».",
    ),
}


def preset(
    habit: Habit, day: date, today: date, style: str, answers: dict[date, bool], recent: list[str]
) -> str:
    """Готовая фраза стиля с цифрами привычки; недавние по возможности не повторяются.

    После пропуска или срыва вместо стиля — поддержка (`AFTER_MISS`).
    """
    kind = "bad" if habit.is_bad else "good"
    marks = {d for d, value in answers.items() if value}
    previous = answers.get(day - timedelta(days=1))
    missed = previous is not None and previous == habit.is_bad
    pool = AFTER_MISS[kind] if missed else PRESETS[style][kind]
    # Удачный день: полезная сделана, вредной не было.
    week = [day - timedelta(days=i) for i in range(7, 0, -1)]
    good_days = sum(1 for d in week if answers.get(d) is (not habit.is_bad))
    values = {
        "name": habit.name,
        "streak": streak(habit, marks, today),
        "best": best_clean_streak(habit, marks, today),
        "week": good_days,
        "week_next": good_days + 1,
    }
    texts = [t.format(**values) for t in pool]
    fresh = [t for t in texts if t not in recent] or texts
    return fresh[(habit.id + day.toordinal()) % len(fresh)]


def clean(text: str) -> str | None:
    """Ответ модели в виде, пригодном для сообщения; пустой или негодный — `None`."""
    value = text.strip().strip("«»\"'").strip()
    if not value:
        return None
    if len(value) > MAX_CHARS:
        cut = value[:MAX_CHARS].rsplit(" ", 1)[0]
        value = cut.rstrip(",;:—-") + "…"
    return value


class Motivator:
    """Пишет мотивационные сообщения и ведёт их журнал."""

    def __init__(
        self,
        storage: Storage,
        api_key: str,
        model: str = DEFAULT_MODEL,
        rng: random.Random | None = None,
    ) -> None:
        self.storage = storage
        self.api_key = api_key
        self.model = model
        self.rng = rng or random.Random()

    async def complete(self, messages: list[dict[str, str]]) -> str | None:
        """Запрос к OpenRouter; при любой ошибке — `None`, напоминание уйдёт без мотивации."""
        # Короткому сообщению рассуждения не нужны: без ограничения модель тратит на них весь
        # лимит токенов и текст приходит пустым или обрезанным.
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 1000,
            "temperature": 0.9,
            "reasoning": {"effort": "low", "exclude": True},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Title": "Habit Trazker",
        }
        try:
            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as session,
                session.post(OPENROUTER_URL, json=payload, headers=headers) as resp,
            ):
                data = await resp.json(content_type=None)
            if resp.status != 200:
                log.warning("openrouter %s: %s", resp.status, data.get("error"))
                return None
            choice = data["choices"][0]
            # Оборванный по лимиту текст хуже готовой фразы.
            if choice.get("finish_reason") not in (None, "stop"):
                log.warning("openrouter finish_reason=%s", choice.get("finish_reason"))
                return None
            return choice["message"]["content"]
        except Exception:
            log.exception("openrouter request failed")
            return None

    async def compose(self, habit: Habit, day: date, today: date) -> tuple[int, str] | None:
        """Сообщение к напоминанию: номер в журнале и текст; `None` — мотивация не нужна или
        не получилась."""
        motivation = self.storage.get_motivation(habit.user_id)
        if motivation.style is None:
            return None
        _, good_for_stage = stage(habit, today)
        style, experiment = choose_style(
            motivation, self.storage.motivation_stats(habit.user_id), good_for_stage, self.rng
        )
        messages = build_messages(
            habit,
            day,
            today,
            motivation,
            style,
            self.storage.all_answers(habit.id),
            self.storage.recent_motivation(habit.id),
        )
        text = clean(await self.complete(messages) or "") if self.api_key else None
        source = "llm"
        if text is None:
            # Модель недоступна — готовая фраза того же стиля, чтобы человек не остался без
            # мотивации, а журнал и кнопки отзыва работали как обычно.
            answers = self.storage.all_answers(habit.id)
            text = preset(
                habit, day, today, style, answers, self.storage.recent_motivation(habit.id)
            )
            source = "preset"
        message_id = self.storage.log_motivation(
            habit.user_id, habit.id, day, style, motivation.tone, experiment, text, source
        )
        return message_id, text
