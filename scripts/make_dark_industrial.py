#!/usr/bin/env python3
"""CLI wrapper: render a procedural dark industrial loop to a WAV file.

All of the synthesis lives in ``shorts_clipper.audio.dark_industrial`` so the
pipeline's automatic BGM fallback
(``shorts_clipper.captions.music.ensure_synthesized_track``) and this script
share exactly one copy of the DSP.  This file only parses arguments and writes
the WAV.

Usage::

    python scripts/make_dark_industrial.py                       # -> data/music/dark_industrial_loop.wav
    python scripts/make_dark_industrial.py --duration 20 --out x.wav
    python scripts/make_dark_industrial.py --bpm 140 --out loop.wav

The loop is original, synthesized material: no external samples and therefore
no license to clear.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from shorts_clipper.audio.dark_industrial import (  # noqa: E402
    BPM,
    DEFAULT_DURATION,
    DEFAULT_FILENAME,
    SAMPLE_RATE,
    make_dark_industrial,
    write_wav,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a procedural dark industrial loop.")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/music") / DEFAULT_FILENAME,
        help=f"Output WAV path (default: data/music/{DEFAULT_FILENAME})",
    )
    parser.add_argument(
        "--duration", type=float, default=DEFAULT_DURATION, help="Length in seconds"
    )
    parser.add_argument("--bpm", type=float, default=BPM, help="Tempo in BPM")
    args = parser.parse_args(argv)

    print(f"Rendering {args.duration}s dark industrial loop at {args.bpm:.1f} BPM ...")
    samples = make_dark_industrial(duration=args.duration, bpm=args.bpm)
    write_wav(args.out, samples)
    n = len(samples)
    print(
        f"Wrote {args.out} ({n} samples, ~{n / SAMPLE_RATE:.1f}s, "
        f"{n / SAMPLE_RATE * SAMPLE_RATE / 1000 / 1000:.1f} MB)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
