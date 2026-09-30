"""Persistent script dedup for stock shorts.

Tracks which quote scripts were already rendered in a small JSON state file
so repeated runs prefer fresh scripts instead of repeating the same quote.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

DEFAULT_USED_PATH = Path("data/stock_used.json")


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
