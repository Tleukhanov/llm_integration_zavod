#!/usr/bin/env python3
"""Download music files into the shorts music directory.

Usage::

    python scripts/fetch_music.py [--dir D:/shorts_music] <url...>

Downloads each URL via ``urllib.request`` (no external dependencies).
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _download(url: str, dest: Path) -> Path:
    """Download *url* into *dest*, returning the final path."""
    name = url.rstrip("/").split("/")[-1].split("?")[0] or "track.mp3"
    out = dest / name
    if out.exists():
        print(f"  skip (exists): {out}")
        return out
    try:
        urllib.request.urlretrieve(url, str(out))
    except (urllib.error.URLError, OSError) as exc:
        print(f"  ERROR downloading {url}: {exc}", file=sys.stderr)
        if out.exists():
            out.unlink()
        raise SystemExit(1)
    print(f"  saved: {out}")
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Download music tracks.")
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path("D:/shorts_music"),
        help="Directory to save tracks (default: D:/shorts_music)",
    )
    parser.add_argument("urls", nargs="+", metavar="URL", help="Audio file URLs")
    args = parser.parse_args(argv)

    args.dir.mkdir(parents=True, exist_ok=True)

    for url in args.urls:
        print(f"Downloading {url} …")
        _download(url, args.dir)

    print("Done.")


if __name__ == "__main__":
    main()
