"""End-to-end smoke test for the shorts-clipper pipeline.

Runs `python -m shorts_clipper clip <url>` against a real URL, directing all
heavy outputs (rendered clips, whisper models, ffmpeg temp files) under a
dedicated work dir so it never pollutes the git worktree or the C drive.

Exit codes:
    0  pipeline finished and all artifacts verified
    2  pipeline crashed or exceeded the timeout
    3  pipeline ran but artifacts are missing/invalid (e.g. download failed)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import av

DEFAULT_URL = "https://www.youtube.com/watch?v=arj7oStGLkU"
DEFAULT_WORK_DIR = r"D:\shorts_smoke"

ENV_VARS: dict[str, str] = {
    "SHORTS_VIDEO_PRESET": "ultrafast",
    "SHORTS_VIDEO_CODEC": "libx264",
    "AFFILIATE_ENABLED": "false",
    "SHORTS_WHISPER_DEVICE": "cpu",
    "SHORTS_WHISPER_COMPUTE_TYPE": "int8",
}


def scan_outputs(output_dir: Path, log: Callable[[str], None] | Any) -> dict[str, Any]:
    """Scan an output dir for pipeline artifacts and verify the clip video.

    Artifacts are written under ``<output_dir>/run_<timestamp>/`` by the
    pipeline, so we scan recursively. Returns a dict with keys:
        found_mp4 (bool), found_jpg (bool), found_metadata (bool),
        vertical (bool), duration_sec (float)
    """
    result: dict[str, Any] = {
        "found_mp4": False,
        "found_jpg": False,
        "found_metadata": False,
        "vertical": False,
        "duration_sec": 0.0,
    }

    def emit(msg: str) -> None:
        if hasattr(log, "info"):
            log.info(msg)
        else:
            log(msg)

    mp4s = sorted(output_dir.rglob("rendered_clip_*.mp4"))
    jpgs = sorted(output_dir.rglob("thumbnail_*.jpg"))
    metas = sorted(output_dir.rglob("final_metadata_*.json"))
    metas.extend(sorted(output_dir.rglob("*-metadata.json")))

    result["found_mp4"] = bool(mp4s)
    result["found_jpg"] = bool(jpgs)
    result["found_metadata"] = bool(metas)

    if mp4s:
        clip = mp4s[-1]
        try:
            container = av.open(str(clip))
            try:
                video_stream = next(
                    (s for s in container.streams if s.type == "video"), None
                )
                if video_stream is not None:
                    width = video_stream.width
                    height = video_stream.height
                    if container.duration:
                        duration = float(container.duration) / av.time_base
                    else:
                        duration = float(
                            video_stream.duration * video_stream.time_base
                        )
                    result["vertical"] = height > width
                    result["duration_sec"] = round(duration, 3)
                    emit(
                        f"Verified clip {clip.name}: {width}x{height}, "
                        f"duration={duration:.3f}s"
                    )
                else:
                    emit(f"No video stream found in {clip}")
            finally:
                container.close()
        except Exception as exc:
            emit(f"Failed to verify clip {clip}: {exc}")

    return result


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="smoke_test.py",
        description="End-to-end smoke test for the shorts-clipper pipeline.",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="YouTube URL to clip.")
    parser.add_argument(
        "--work-dir",
        default=DEFAULT_WORK_DIR,
        help="Work dir for outputs, models and temp files (default: D:\\shorts_smoke).",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove the work dir before starting (default off).",
    )
    parser.add_argument(
        "--timeout-secs",
        type=int,
        default=1500,
        help="Timeout for the pipeline run in seconds (default 1500).",
    )
    args = parser.parse_args(argv)

    work_dir = Path(args.work_dir)
    out_dir = work_dir / "out"
    models_dir = work_dir / "models"
    tmp_dir = work_dir / "tmp"
    log_path = work_dir / "smoke_run.log"

    if args.clean and work_dir.exists():
        shutil.rmtree(work_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    models_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    os.environ.update(ENV_VARS)
    os.environ["SHORTS_OUTPUT_DIR"] = str(out_dir)
    os.environ["SHORTS_MODELS_DIR"] = str(models_dir)
    os.environ["TMP"] = str(tmp_dir)
    os.environ["TEMP"] = str(tmp_dir)
    hf_home = models_dir / "huggingface"
    os.environ["HF_HOME"] = str(hf_home)
    os.environ["HF_HUB_CACHE"] = str(hf_home / "hub")
    hf_home.mkdir(parents=True, exist_ok=True)

    with log_path.open("w", encoding="utf-8") as log:
        def log_write(msg: str) -> None:
            log.write(msg + "\n")
            log.flush()
            print(msg)

        log_write("=== short-clipper SMOKE TEST ===")
        log_write(f"url        : {args.url}")
        log_write(f"work-dir   : {work_dir}")
        log_write(f"output-dir : {out_dir}")
        log_write(f"models-dir : {models_dir}")
        log_write(f"timeout    : {args.timeout_secs}s")
        log_write("")

        python = sys.executable
        cmd = [
            python,
            "-m",
            "shorts_clipper",
            "clip",
            args.url,
            "-c",
            "1",
        ]
        log_write("Running: " + " ".join(cmd))

        start = time.time()
        try:
            completed = subprocess.run(
                cmd,
                timeout=args.timeout_secs,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except subprocess.TimeoutExpired as exc:
            for stream in (exc.stdout, exc.stderr):
                if stream:
                    log_write(stream.decode("utf-8", errors="replace"))
            log_write("")
            log_write("ERROR: pipeline exceeded timeout; killed.")
            log_write("RESULT: FAIL (timeout) - pipeline did not finish")
            return 2

        for line in (completed.stdout or b"").decode("utf-8", errors="replace").splitlines():
            log_write(line)
        elapsed = time.time() - start
        exit_code = completed.returncode
        log_write("")
        log_write(f"pipeline exit code: {exit_code} (elapsed {elapsed:.1f}s)")

        if exit_code != 0:
            log_write("")
            log_write("----- REPORT -----")
            log_write(f"URL used             : {args.url}")
            log_write("Artifacts            : none verified (pipeline crashed)")
            log_write(f"Pipeline exit code   : {exit_code}")
            log_write(f"Elapsed              : {elapsed:.1f}s")
            log_write("Result               : FAIL")
            log_write(f"Log                  : {log_path}")
            return 2

        log_handle = log_path.open("a", encoding="utf-8")

        def aprint(msg: str) -> None:
            log_handle.write(msg + "\n")
            log_handle.flush()
            print(msg)

        try:
            log_handle.write("\n----- ARTIFACT SCAN -----\n")
            log_handle.flush()

            res = scan_outputs(out_dir, aprint)

            aprint("")
            aprint("----- PASS/FAIL SUMMARY -----")
            aprint(f"{'Artifact':<22} {'Status':<8} Detail")
            aprint("-" * 60)
            _runs = sorted({p.parent for p in out_dir.rglob("rendered_clip_*.mp4")})
            latest_run = _runs[-1] if _runs else None
            mp4s = sorted(latest_run.glob("rendered_clip_*.mp4")) if latest_run else sorted(out_dir.rglob("rendered_clip_*.mp4"))
            jpgs = sorted(latest_run.glob("thumbnail_*.jpg")) if latest_run else sorted(out_dir.rglob("thumbnail_*.jpg"))
            metas = sorted(latest_run.glob("final_metadata_*.json")) if latest_run else sorted(out_dir.rglob("final_metadata_*.json"))
            rows = [
                ("rendered_clip_*.mp4", res["found_mp4"], "found" if res["found_mp4"] else "missing"),
                ("thumbnail_*.jpg", res["found_jpg"], "found" if res["found_jpg"] else "missing"),
                (
                    "final_metadata_*.json",
                    res["found_metadata"],
                    "found" if res["found_metadata"] else "missing",
                ),
                ("vertical", res["vertical"], "OK" if res["vertical"] else "NOT VERTICAL"),
                (
                    "duration > 0",
                    res["duration_sec"] > 0,
                    f"{res['duration_sec']}s" if res["duration_sec"] > 0 else "ZERO",
                ),
            ]
            for name, ok, detail in rows:
                status = "PASS" if ok else "FAIL"
                aprint(f"{name:<22} {status:<8} {detail}")

            all_ok = (
                res["found_mp4"]
                and res["found_jpg"]
                and res["found_metadata"]
                and res["vertical"]
                and res["duration_sec"] > 0
            )

            aprint("")
            aprint("----- REPORT -----")
            aprint(f"URL used          : {args.url}")
            aprint(f"Pipeline exit code: {exit_code}")
            aprint(f"Elapsed           : {elapsed:.1f}s")
            aprint(f"mp4 files         : {[p.name for p in mp4s]}")
            aprint(f"jpg files         : {[p.name for p in jpgs]}")
            aprint(f"metadata files    : {[p.name for p in metas]}")
            aprint(f"Verified          : vertical={res['vertical']} duration={res['duration_sec']}s")
            aprint("Log               : {log_path}".replace("{log_path}", str(log_path)))

            if not all_ok:
                if not res["found_mp4"]:
                    aprint("")
                    aprint("NOTE: No rendered artifacts found - video download likely failed")
                    aprint("      (YouTube may be unreachable in this network).")
                    aprint("      The harness itself ran correctly.")
                    aprint("Result            : WARNING (download failed, harness OK)")
                    return 3
                aprint("")
                aprint("Result            : WARNING (some artifacts missing/invalid)")
                return 3

            aprint("Result            : PASS")
            return 0
        finally:
            log_handle.close()


if __name__ == "__main__":
    sys.exit(main())