"""Persistent script dedup for stock shorts.

Tracks which quote scripts were already rendered in a small JSON state file
so repeated runs prefer fresh scripts instead of repeating the same quote.

Two guards share this module: the persistent hash history above (across runs)
and :class:`RecentScripts` below (within a single run).  Near-duplicates are
caught through a *theme* fingerprint — the set of content words of a quote —
so two differently worded scripts about the same 5:00 alarm do not land
back to back either.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path

DEFAULT_USED_PATH = Path("data/stock_used.json")

# Content words shorter than this carry no theme signal ("раз", "тебя").
_THEME_MIN_LEN = 4
# Russian inflections share a long prefix, so a fixed prefix is a cheap and
# stable stand-in for a stemmer: "зарплата"/"зарплату" both give "зарпла".
_THEME_STEM = 6
# Jaccard overlap of two theme fingerprints above which two quotes are treated
# as the same theme.  Calibrated against the shipped banks: rewriting one word
# scores ~0.67 and nesting one quote inside another ~0.83, while the closest
# genuinely-distinct pair in the 271 niche scripts scores 0.31 and unrelated
# quotes score 0.0.
THEME_SIMILARITY = 0.3

_WORD_SPLIT = re.compile(r"[^0-9a-zA-Zа-яА-ЯёЁ]+")

# Function words carry no theme: they appear in every quote of a niche.
_THEME_STOPWORDS = frozenset(
    """
    это этого этом этот эту эти это они она оно он она мы вы ты мне мной
    нас вас им ими себя себе собой нашей ваш его её им их ней нём не него
    в во со с у к о об от из за до по же бы ли бы не ни ну вот там тут
    где куда как так или и а но да все всё весь вся каждое который которая
    ещё ещё же уже бывает был была были было будут будет можно надо нельзя
    может можно очень сам сама сами само самый самые самых только лишь почти
    при про над под перед между среди через после до около после снова
    сейчас сегодня завтра вчера утром вечером днём ночью затем потому
    поэтому если чтобы чтоб пока снова чуть даже будто вроде именно между
    the of and a to in is it you that he she was for on are with as at
    but be this have from or one had by not but they you we
    """.split()
)


def normalize_script(text: str) -> str:
    """Collapse whitespace and casefold for stable comparison."""
    return " ".join(str(text or "").split()).casefold()


def script_hash(text: str) -> str:
    """Return the sha256 hex digest of the normalized script."""
    return hashlib.sha256(normalize_script(text).encode("utf-8")).hexdigest()


def _read_used_list(path: str | Path) -> list[str]:
    """Return ordered used hashes, empty on any read or parse error."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(raw, list):
        return [h for h in raw if isinstance(h, str) and h]
    if isinstance(raw, dict):
        for key in ("order", "hashes", "used"):
            order = raw.get(key)
            if isinstance(order, list):
                return [h for h in order if isinstance(h, str) and h]
    return []


def load_used(path: str | Path) -> set[str]:
    """Return used script hashes, empty when missing or corrupt."""
    return set(_read_used_list(path))


def record_used(path: str | Path, digest: str) -> None:
    """Append a script hash to the used file, never raising."""
    try:
        if not isinstance(digest, str) or not digest:
            return
        target = Path(path)
        order = _read_used_list(target)
        if digest in order:
            order.remove(digest)
        order.append(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(order, indent=2), encoding="utf-8")
        tmp.replace(target)
    except Exception:
        return


def choose_unused(
    candidates: list[str],
    used: set[str] | list[str],
) -> tuple[str, bool]:
    """Pick the first candidate not in used.

    Return a ``(script, reset)`` tuple where ``reset`` is True only when
    every candidate was already used. On reset the first candidate is
    returned for reuse and the caller starts a new cycle by replacing the
    history with just that script, which keeps reuse in least-recently-used
    order while the pool order stays stable.
    """
    if not candidates:
        raise ValueError("candidates must not be empty")
    used_set = set(used or [])
    for candidate in candidates:
        if script_hash(candidate) not in used_set:
            return candidate, False
    if isinstance(used, list):
        order = [h for h in used if isinstance(h, str)]
        position = {h: i for i, h in enumerate(order)}
        best = min(candidates, key=lambda c: position.get(script_hash(c), 0))
        return best, True
    return candidates[0], True


def theme_tokens(text: str) -> frozenset[str]:
    """Content words of *text*, stemmed by a fixed prefix.

    This is the fingerprint used to notice that two differently worded quotes
    talk about the same thing, so one 5:00-alarm script does not follow
    another 5:00-alarm script.  Digits and short/function words are dropped:
    they are either pure noise or shared by every quote in the niche.
    """
    out: set[str] = set()
    for raw in _WORD_SPLIT.split(normalize_script(text)):
        token = raw.strip()
        if len(token) < _THEME_MIN_LEN or not token[0].isalpha():
            continue
        if token in _THEME_STOPWORDS:
            continue
        out.add(token[:_THEME_STEM])
    return frozenset(out)


def theme_hash(text: str) -> str:
    """Stable digest of the sorted theme tokens."""
    return hashlib.sha256(" ".join(sorted(theme_tokens(text))).encode("utf-8")).hexdigest()


def _overlap(left: frozenset[str], right: frozenset[str]) -> float:
    """Jaccard overlap of two theme fingerprints, from 0.0 to 1.0."""
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def theme_similarity(left: str, right: str) -> float:
    """Share of theme words between two scripts, from 0.0 to 1.0."""
    return _overlap(theme_tokens(left), theme_tokens(right))


class RecentScripts:
    """Bounded within-run history that suppresses repeats.

    :func:`choose_unused` stops the same text from repeating across runs;
    this stops it repeating inside one batch, where every clip is rendered
    minutes apart, and also stops a *near*-duplicate (same theme, different
    wording) from landing next to it.
    """

    def __init__(self, limit: int = 4, similarity: float = THEME_SIMILARITY) -> None:
        self.limit = max(1, int(limit))
        self.similarity = float(similarity)
        self._scripts: list[str] = []
        self._themes: list[frozenset[str]] = []

    def __len__(self) -> int:
        return len(self._scripts)

    def add(self, script: str) -> None:
        """Record a rendered script, evicting the oldest past the limit."""
        self._scripts.append(script)
        self._themes.append(theme_tokens(script))
        while len(self._scripts) > self.limit:
            self._scripts.pop(0)
            self._themes.pop(0)

    def is_same(self, script: str) -> bool:
        """True when *script* itself was already rendered recently."""
        digest = script_hash(script)
        return any(script_hash(s) == digest for s in self._scripts)

    def is_same_theme(self, script: str) -> bool:
        """True when *script* repeats a recent theme, wording aside."""
        tokens = theme_tokens(script)
        if not tokens:
            return False
        return any(
            seen and (seen == tokens or _overlap(tokens, seen) >= self.similarity)
            for seen in self._themes
        )

    def conflicts(self, script: str) -> bool:
        """True when *script* may not follow the recent scripts."""
        return self.is_same(script) or self.is_same_theme(script)

    def guard(self, candidates: list[str]) -> list[str]:
        """Candidates that neither repeat nor share a recent theme.

        The guard is advisory, never absolute: when every candidate conflicts
        the untouched pool is returned so a thin pool can still render.
        """
        fresh = [c for c in candidates if not self.is_same(c)]
        kept = [c for c in fresh if not self.is_same_theme(c)]
        return kept or fresh or list(candidates)


def choose_fresh(
    candidates: list[str],
    used: set[str] | list[str],
    *,
    seed: int = 0,
    recent: RecentScripts | None = None,
) -> tuple[str, bool]:
    """Pick an unused, non-repeating candidate; seeded for variety.

    :func:`choose_unused` owns the exhaustion semantics (an entirely used pool
    restarts the cycle, returning the least-recently-used entry) so the
    persistent history keeps its exact meaning.  Within one cycle the pick is
    instead drawn from every unused candidate with ``seed``, which keeps the
    batch varied while staying reproducible for a given run seed, and
    *recent* drops anything that would repeat the previous clips.
    """
    anchor, reset = choose_unused(candidates, used)
    if reset:
        return anchor, True
    used_set = set(used or [])
    fresh = [c for c in candidates if script_hash(c) not in used_set]
    if recent is not None:
        fresh = recent.guard(fresh)
    if not fresh:
        return anchor, False
    return random.Random(seed).choice(fresh), False
