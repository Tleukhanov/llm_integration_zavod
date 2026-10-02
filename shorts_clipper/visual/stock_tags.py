"""Semantic tags for stock backgrounds and quote scripts.

A background clip and a spoken script are matched through one small, explicit
tag vocabulary instead of an unrelated random draw:

    data/stock/self-growth/city_night_rain.mp4   -> {city, city_night, rain}
    "Пять утра. Двенадцать подтягиваний..."     -> {morning, training}

Everything here is pure stdlib, deterministic and reviewable: the vocabulary
and both keyword tables are literals in this file, so any selection decision
can be explained by pointing at a line.  No model, no embeddings, no deps.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tag vocabulary
# ---------------------------------------------------------------------------

# A background clip carries a visual tag when any of its tokens appears in the
# clip filename or in the Pexels cache folder that holds it, so
# ``_pexels_cache/morning-sunrise-forest-calm/00.mp4`` is tagged from the query
# slug the clips were downloaded for, not from the meaningless ``00`` filename.
VISUAL_TAG_TOKENS: dict[str, tuple[str, ...]] = {
    "city": ("city", "cities", "urban", "street", "downtown", "metropolis", "buildings"),
    "city_night": ("city_night", "noir"),
    "night": ("night", "nights", "nighttime", "midnight", "nightfall"),
    "neon": ("neon", "cyberpunk", "signs", "signage"),
    "office": ("office", "desk", "workspace", "meeting", "business", "corporate", "colleagues"),
    "laptop": ("laptop", "notebook", "macbook", "computer"),
    "phone": ("phone", "smartphone", "screen", "scrolling", "notifications", "chat"),
    "car": ("car", "cars", "drive", "driving", "taxi", "traffic"),
    "highway": ("highway", "freeway", "motorway", "autobahn", "roadtrip"),
    "train": ("train", "subway", "station", "railway", "platform", "metro"),
    "crowd": ("crowd", "commuters", "people", "pedestrians"),
    "rain": ("rain", "rainy", "storm", "puddle", "wet"),
    "snow": ("snow", "snowy", "winter", "frost"),
    "fog": ("fog", "foggy", "mist", "misty", "haze", "hazy"),
    "smoke": ("smoke", "smoking", "cigarette", "ash", "fumes"),
    "water": ("water", "waves", "wave", "river", "stream"),
    "ocean": ("ocean", "sea", "beach", "shore", "coast", "surf", "horizon"),
    "forest": ("forest", "woods", "trees", "tree", "jungle"),
    "mountain": ("mountain", "mountains", "peak", "valley", "cliff", "hiking"),
    "field": ("field", "fields", "grass", "meadow", "countryside"),
    "sky": ("sky", "clouds", "aerial", "drone"),
    "sunrise": ("sunrise", "daybreak", "dawn"),
    "morning": ("morning", "sunup"),
    "sunset": ("sunset", "dusk", "twilight", "golden"),
    "window": ("window", "windows", "blinds", "curtain"),
    "money": ("money", "cash", "dollar", "coins", "wallet"),
    "cafe": ("cafe", "coffee", "kitchen", "restaurant", "bar"),
    "gym": ("gym", "workout", "weights", "dumbbell", "treadmill"),
    "couple": ("couple", "romance", "love", "hug", "kiss", "wedding", "partners"),
    "home": ("home", "apartment", "bedroom", "room"),
    "stress": ("stress", "pressure", "tense", "anxious", "worry"),
    "calm": ("calm", "quiet", "minimal", "still", "silence"),
}

# A script carries a tag when any of its keywords appears in the text.  The
# keywords are stems, matched as substrings, so one entry covers the Russian
# inflections ("дорог" also matches "дороге", "дороги") without a stemmer.
SCRIPT_TAG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "morning": ("утр", "рано", "рассвет", "подъём", "подъем", "проснул", "будильник"),
    "night": ("ноч", "полноч", "поздн", "бессонниц", "не спал", "не спит", "23:00"),
    "sunrise": ("рассвет", "восход", "первый свет", "на заре"),
    "sunset": ("закат", "сумерк", "вечерн", "под вечер"),
    "city": ("город", "метро", "улиц", "переул", "район", "двор", "районы"),
    "city_night": ("фонар", "пробк", "трафик", "машин едут"),
    "neon": ("неон", "светящ", "реклам", "вывеск"),
    "office": ("работ", "офис", "коллег", "начальник", "совещан", "встреч", "задач"),
    "laptop": ("ноутбук", "компьютер", "экран", "монитор", "почт", "письм", "документ"),
    "phone": ("телефон", "лента", "уведомлен", "сообщени", "входящ", "звонок", "звонит", "позвон", "чат"),
    "car": ("автомобил", "машин", "руль", "водител", "парков", "ездил"),
    "highway": ("шоссе", "трасс", "дорог", "пробег", "километр"),
    "train": ("поезд", "метро", "вокзал", "перрон", "станци"),
    "crowd": ("толпа", "толпу", "очеред", "людей", "все вокруг"),
    "rain": ("дожд", "ливень", "мокр"),
    "snow": ("снег", "мороз", "зим"),
    "fog": ("туман", "дымк"),
    "smoke": ("дым", "сигарет", "курит", "курить", "тлеющ", "пепельниц"),
    "water": ("вода", "воды", "река", "ручей", "течёт", "течет"),
    "ocean": ("океан", "море", "морск", "волн", "прилив", "пляж", "берег", "горизонт"),
    "forest": ("лес", "дерев", "хво", "тропа", "опен"),
    "mountain": ("горы", "гора", "гору", "горный", "горной", "вершин", "скал", "хребет", "альп"),
    "field": ("поле", "луг", "трава", "деревн"),
    "sky": ("небо", "облак", "с неба"),
    "window": ("окно", "окна", "штор"),
    "money": (
        "деньг", "денег", "зарплат", "заработ", "доход", "рубл", "копил", "долг",
        "расход", "оплат", "цена", "цену", "дорого", "бюджет", "прибыл", "клиент",
        "чек", "вклад",
    ),
    "cafe": ("кофе", "чашк", "кухн", "ресторан", "кафе", "завтрак", "обед"),
    "gym": ("спортзал", "трениров", "подтягиван", "отжим", "штанга", "мышц", "бегать", "пробеж"),
    "couple": (
        "любов", "люблю", "любишь", "любит", "любил", "влюб", "отношен", "чувств",
        "обнима", "поцелу", "муж", "жена", "парень", "девуш", "свидан", "роман",
        "скуча", "вместе",
    ),
    "home": ("дом", "квартир", "комнат", "спальн", "уют"),
    "stress": ("стресс", "тревог", "тревож", "перегруз", "выгоран", "устал"),
    "calm": ("тишин", "спокой", "тихо", "медленн", "терпени", "покой"),
}

_TOKENS_SPLIT = re.compile(r"[^0-9a-zA-Zа-яА-ЯёЁ]+")


def _tokens(text: str) -> list[str]:
    """Lowercase alphanumeric tokens of *text*, order preserved."""
    return [tok.casefold() for tok in _TOKENS_SPLIT.split(text) if tok.isalnum()]


def visual_tag_names() -> tuple[str, ...]:
    """Every tag in the visual vocabulary, for docs and diagnostics."""
    return tuple(VISUAL_TAG_TOKENS)


def read_tag_sidecar(clip: str | Path) -> list[str] | None:
    """Explicit tags for *clip* from a sidecar, or ``None`` when absent.

    Two sidecar shapes are supported so an operator can hand-write either:
    ``<clip>.json`` with a ``tags`` list/string (or a bare JSON list) and
    ``<clip>.tags.txt`` with one tag per line or separated by spaces/commas.
    """
    path = Path(clip)
    name = str(path)[: -len(path.suffix)] if path.suffix else str(path)
    for candidate, parser in ((name + ".json", _sidecar_json), (name + ".tags.txt", _sidecar_text)):
        sidecar = Path(candidate)
        try:
            if not sidecar.is_file():
                continue
            tags = parser(sidecar)
        except Exception as exc:
            log.warning("Ignoring unreadable tag sidecar %s: %s", sidecar, exc)
            continue
        if tags:
            return tags
    return None


def _sidecar_json(path: Path) -> list[str]:
    """Tags from a JSON sidecar."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("tags")
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    if not isinstance(raw, list):
        return []
    return [str(t).strip().casefold().replace(" ", "_") for t in raw if str(t).strip()]


def _sidecar_text(path: Path) -> list[str]:
    """Tags from a plain-text sidecar (one per line, ``#`` comments skipped)."""
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        for part in line.replace(",", " ").split():
            out.append(part.casefold())
    return out


def background_tags(clip: str | Path) -> frozenset[str]:
    """Semantic tags for one background clip.

    An explicit sidecar (``<clip>.json`` / ``<clip>.tags.txt``) always wins and
    is taken verbatim.  Otherwise tags are inferred from the filename stem and
    the parent folder name, which is what makes Pexels cache entries like
    ``_pexels_cache/morning-sunrise-forest-calm/00.mp4`` carry the semantics
    of the query they were downloaded for.
    """
    path = Path(clip)
    sidecar = read_tag_sidecar(path)
    if sidecar:
        return frozenset(sidecar)
    return tags_from_tokens(_tokens(f"{path.parent.name} {path.stem}"))


def tags_from_tokens(tokens: list[str] | tuple[str, ...]) -> frozenset[str]:
    """Visual tags implied by *tokens* (see :data:`VISUAL_TAG_TOKENS`).

    Adjacent pairs count too, joined with an underscore, so compound entries
    work on the ``city_night_rain.mp4`` naming people already use.
    """
    words = list(tokens)
    present = set(words)
    present.update(f"{a}_{b}" for a, b in zip(words, words[1:], strict=False))
    return frozenset(
        tag
        for tag, tag_tokens in VISUAL_TAG_TOKENS.items()
        if present.intersection(tag_tokens)
    )


def background_pool_tags(clips: list[Path] | None) -> frozenset[str]:
    """Tags of the clip that opens the short.

    Only the primary (first) clip is considered: it sets the semantic frame
    the viewer reads first, while a later montage clip must not drag the quote
    into a different topic.
    """
    if not clips:
        return frozenset()
    try:
        return background_tags(Path(clips[0]))
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("Could not tag background %s: %s", clips[0], exc)
        return frozenset()


def script_tags(text: str) -> frozenset[str]:
    """Tags implied by a script body (see :data:`SCRIPT_TAG_KEYWORDS`)."""
    if not text:
        return frozenset()
    haystack = str(text).casefold()
    return frozenset(
        tag for tag, keywords in SCRIPT_TAG_KEYWORDS.items() if any(kw in haystack for kw in keywords)
    )


def filter_scripts_by_tags(scripts: list[str], tags: frozenset[str]) -> tuple[list[str], bool]:
    """Scripts sharing at least one tag with *tags*.

    Returns ``(matched, fell_back)``.  When no script matches — untagged clip,
    or a niche whose quotes are all off-vocabulary — the full pool is returned
    unchanged with ``fell_back=True`` so the caller can log the miss.
    """
    pool = [s for s in scripts if s]
    if not tags:
        return pool, False
    matched = [s for s in pool if script_tags(s) & tags]
    if matched:
        return matched, False
    return pool, True