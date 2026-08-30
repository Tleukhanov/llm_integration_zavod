"""Stream-piped video rendering module.

Pipes the yt-dlp download output directly into FFmpeg stdin, running
scale, crop, speed/pacing, and subtitle burning in a single process loop.
This completely bypasses writing heavy intermediate video clips to disk.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path

from shorts_clipper.captions.generator import generate_ass_file
from shorts_clipper.core.models import TranscriptSegment
from shorts_clipper.cropping.geometry import CropBox
from shorts_clipper.downloader.yt_dlp import get_base_yt_dlp_cmd
from shorts_clipper.utils.ffmpeg_path import ffmpeg_path

log = logging.getLogger(__name__)


_TARGET_W = 1080
_TARGET_H = 1920


def get_url_dimensions(url: str) -> tuple[int, int]:
    """Fetch video width and height from YouTube URL using yt-dlp without downloading."""
    log.info("🔍 Probing video dimensions for %s...", url)
    cmd = get_base_yt_dlp_cmd()
    cmd.extend(
        [
            "--skip-download",
            "--dump-json",
            "--socket-timeout",
            "10",
            url,
        ]
    )
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            width = data.get("width")
            height = data.get("height")
            if width and height:
                log.info("Fetched dimensions: %dx%d", width, height)
                return int(width), int(height)
    except Exception as e:
        log.warning("Failed to fetch dimensions from url: %s. Using default 1920x1080.", e)
    return 1920, 1080


def run_face_detection(url: str, start_time: float, end_time: float) -> int | None:
    """Download a tiny 3-second sample clip, extract frames, and detect speaker face center."""
    try:
        import cv2
    except ImportError:
        log.warning("⚠️  OpenCV (opencv-python-headless) not installed. Face tracking disabled.")
        return None

    log.info("🤖 Running auto face-tracking detection...")
    with tempfile.TemporaryDirectory(prefix="face_detect_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        sample_path = tmp_path / "sample.mp4"

        # Download 3s low-res clip
        cmd = get_base_yt_dlp_cmd()
        cmd.extend(
            [
                "-f",
                "worstvideo[ext=mp4][height<=360]/worst",
                "--download-sections",
                f"*{start_time}-{start_time + 3.0}",
                "-o",
                str(sample_path),
                url,
            ]
        )
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=25)
        except Exception as err:
            log.warning("Failed to download sample clip for face tracking: %s", err)
            return None

        # Extract 3 frames
        try:
            cap = cv2.VideoCapture(str(sample_path))
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if frame_count <= 0:
                return None

            face_cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
            x_centers = []

            for index in [10, frame_count // 2, frame_count - 10]:
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, min(index, frame_count - 1)))
                ret, frame = cap.read()
                if not ret:
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = face_cascade.detectMultiScale(gray, 1.2, 4)
                for x, _y, w, _h in faces:
                    x_centers.append(x + w // 2)

            cap.release()
            if x_centers:
                # Map sample pixel coordinate back to relative ratio
                cap_ref = cv2.VideoCapture(str(sample_path))
                vid_width = cap_ref.get(cv2.CAP_PROP_FRAME_WIDTH)
                cap_ref.release()
                if vid_width > 0:
                    avg_center_ratio = sum(x_centers) / len(x_centers) / vid_width
                    log.info(
                        "🎯 Detected speaker center at %.2f%% width",
                        avg_center_ratio * 100,
                    )
                    return avg_center_ratio
        except Exception as face_err:
            log.warning("Error running face detection cascade: %s", face_err)
    return None


def run_dynamic_face_tracking(
    url: str,
    start_time: float,
    end_time: float,
    src_w: int,
    src_h: int,
    crop_width: int,
    step_seconds: float = 2.0,
) -> str | None:
    """Download low-res video, run face detection every step_seconds, and build an FFmpeg panning expression."""
    try:
        import cv2
    except ImportError:
        log.warning(
            "⚠️ OpenCV (opencv-python-headless) not installed. Dynamic face panning disabled."
        )
        return None

    duration = end_time - start_time
    if duration <= 0:
        return None

    log.info("🤖 Starting dynamic face-tracking analysis across %.1f seconds...", duration)
    with tempfile.TemporaryDirectory(prefix="face_pan_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        sample_path = tmp_path / "sample.mp4"

        # Download low-res segment
        cmd = get_base_yt_dlp_cmd()
        cmd.extend(
            [
                "-f",
                "worstvideo[ext=mp4][height<=240]/worst",
                "--download-sections",
                f"*{start_time}-{end_time}",
                "-o",
                str(sample_path),
                url,
            ]
        )
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        except Exception as err:
            log.warning("Failed to download sample clip for dynamic face tracking: %s", err)
            return None

        try:
            cap = cv2.VideoCapture(str(sample_path))
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            sample_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
            if fps <= 0 or frame_count <= 0 or sample_w <= 0:
                cap.release()
                return None

            face_cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )

            # Sample every step_seconds
            raw_x: list[float] = []
            timestamps: list[float] = []

            num_steps = int(duration / step_seconds) + 1
            last_known_x = (src_w - crop_width) // 2

            for i in range(num_steps):
                t_rel = i * step_seconds
                if t_rel > duration:
                    t_rel = duration

                frame_idx = int(t_rel * fps)
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, min(frame_idx, frame_count - 1)))
                ret, frame = cap.read()
                if not ret:
                    continue

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = face_cascade.detectMultiScale(gray, 1.2, 4)

                if len(faces) > 0:
                    # Average x-center of all detected faces
                    avg_face_x = sum(x + w // 2 for x, _y, w, _h in faces) / len(faces)
                    # Map to source resolution
                    face_src_x = avg_face_x * (src_w / sample_w)
                    # Center the crop box on face
                    target_x = face_src_x - crop_width // 2
                    # Clamp
                    target_x = max(0.0, min(target_x, src_w - crop_width))
                    last_known_x = target_x

                raw_x.append(last_known_x)
                timestamps.append(t_rel)

            cap.release()

            if not raw_x:
                return None

            # Smooth the tracking coordinates with a moving average filter
            smoothed_x = []
            for i in range(len(raw_x)):
                neighbors = [raw_x[j] for j in range(max(0, i - 1), min(len(raw_x), i + 2))]
                smoothed_x.append(sum(neighbors) / len(neighbors))

            # Build recursion for piecewise linear interpolation expression
            points = list(zip(timestamps, smoothed_x, strict=True))

            def build_expr(idx: int) -> str:
                if idx == len(points) - 1:
                    return f"{points[idx][1]:.2f}"
                t_curr, x_curr = points[idx]
                t_next, x_next = points[idx + 1]
                slope = (x_next - x_curr) / (t_next - t_curr)
                rest = build_expr(idx + 1)
                return f"if(lt(t,{t_next:.2f}),{x_curr:.2f}+{slope:.4f}*(t-{t_curr:.2f}),{rest})"

            expr = build_expr(0)
            log.info("🎯 Generated dynamic panning FFmpeg expression (length=%d)", len(expr))
            return expr

        except Exception as err:
            log.warning("Error computing dynamic face tracking: %s", err)
            return None


def compute_custom_crop(
    width: int, height: int, layout: str, url: str, start_time: float, end_time: float
) -> CropBox:
    """Compute crop coordinates supporting custom layout offsets and face tracking."""
    target_ratio = _TARGET_W / _TARGET_H

    # Calculate initial crop box fill sizing
    source_ratio = width / height
    if source_ratio > target_ratio:
        crop_height = height
        crop_width = max(1, round(height * target_ratio))
        y = 0

        # Center x offset
        center_x = (width - crop_width) // 2
        x: int | str = center_x

        # Parse layout offset settings
        if layout.startswith("custom_offset_"):
            try:
                # E.g. "custom_offset_-150" or "custom_offset_200"
                offset_val = int(layout.split("_")[-1])
                # Shift relative to center
                x = center_x + offset_val
                logger_msg = f"Applying manual crop offset shift of {offset_val}px"
                log.info(logger_msg)
            except Exception:
                pass
        elif layout == "crop_left":
            x = 0
        elif layout == "crop_right":
            x = width - crop_width
        elif layout == "auto_track":
            center_ratio = run_face_detection(url, start_time, end_time)
            if center_ratio is not None:
                # Align crop box around face center
                face_x = round(width * center_ratio)
                x = face_x - crop_width // 2
            else:
                log.info("Face tracking fallback: using center crop.")
                x = center_x
        elif layout == "auto_pan":
            expr = run_dynamic_face_tracking(url, start_time, end_time, width, height, crop_width)
            if expr:
                return CropBox(x=expr, y=0, width=crop_width, height=crop_height)
            else:
                log.info("Dynamic face panning fallback: using center crop.")
                x = center_x
    else:
        crop_width = width
        crop_height = max(1, round(width / target_ratio))
        x = 0
        y: int | str = (height - crop_height) // 2

    # Clamp bounds to verify it is within video dimensions (only if it is static int)
    if isinstance(x, (int, float)):
        x = max(0, min(int(x), width - crop_width))
    if isinstance(y, (int, float)):
        y = max(0, min(int(y), height - crop_height))

    return CropBox(x=x, y=y, width=crop_width, height=crop_height)


def stream_render_pipeline(
    url: str,
    start_time: float,
    end_time: float,
    layout: str,
    segments: list[TranscriptSegment],
    output_path: Path,
    pacing: float = 1.15,
    video_codec: str = "libx264",
    preset: str = "ultrafast",
    subtitle_style: str = "default",
    enable_gpu: bool = False,
) -> Path:
    """Run pipelined stream transcode: yt-dlp -> ffmpeg (crop, speed, ASS subtitles) -> file."""
    output_path = Path(output_path)

    # 1. Fetch dimensions
    src_w, src_h = get_url_dimensions(url)

    if src_w <= 0 or src_h <= 0:
        raise ValueError(f"Invalid probed video dimensions: {src_w}x{src_h}")

    # Scale probed dimensions to fit within the download format limit (height <= 1080)
    # matching the yt-dlp format selection [height<=1080]
    if src_h > 1080:
        scale = 1080 / src_h
        src_w = round(src_w * scale)
        src_h = 1080
        log.info(
            "Scaled probed dimensions to download format resolution: %dx%d (scale: %.4f)",
            src_w,
            src_h,
            scale,
        )

    # 2. Compute custom crop box
    crop = compute_custom_crop(src_w, src_h, layout, url, start_time, end_time)

    # Validate crop dimensions to prevent FFmpeg filter graph out-of-bounds error (exit 224)
    if isinstance(crop.width, (int, float)) and crop.width > src_w:
        raise ValueError(f"Crop width {crop.width} exceeds video width {src_w}")
    if isinstance(crop.height, (int, float)) and crop.height > src_h:
        raise ValueError(f"Crop height {crop.height} exceeds video height {src_h}")

    if isinstance(crop.x, (int, float)):
        if crop.x < 0 or crop.x + crop.width > src_w:
            raise ValueError(
                f"Crop x coordinate {crop.x} (width {crop.width}) is out of bounds for video width {src_w}"
            )
    if isinstance(crop.y, (int, float)):
        if crop.y < 0 or crop.y + crop.height > src_h:
            raise ValueError(
                f"Crop y coordinate {crop.y} (height {crop.height}) is out of bounds for video height {src_h}"
            )

    log.info(
        "Valid crop box computed and verified: crop=%dx%d at x=%s, y=%s",
        crop.width,
        crop.height,
        crop.x,
        crop.y,
    )

    # 3. Create temporary ASS subtitles file
    with tempfile.TemporaryDirectory(prefix="pipe_ass_") as tmp_dir:
        ass_path = Path(tmp_dir) / "subs.ass"
        generate_ass_file(segments, 0.0, ass_path, pacing=pacing, style_name=subtitle_style)

        # Escape paths
        escaped_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")

        # Build complex filters
        vf_parts = [
            f"crop={crop.width}:{crop.height}:{crop.x}:{crop.y}",
            f"scale={_TARGET_W}:{_TARGET_H}",
        ]

        af_parts = [
            "acompressor=threshold=-20dB:ratio=4:makeup=4",
            "aformat=channel_layouts=stereo",
        ]

        if pacing != 1.0:
            pts_factor = round(1.0 / pacing, 6)
            vf_parts.append(f"setpts={pts_factor}*PTS")
            af_parts.insert(0, f"atempo={pacing}")

        vf_parts.append(f"ass='{escaped_ass}'")

        # Construct yt-dlp stream command
        fmt = "bestvideo[ext=mp4][height<=1080][vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        yt_cmd = get_base_yt_dlp_cmd()
        yt_cmd.extend(
            [
                "--retries",
                "5",
                "--socket-timeout",
                "15",
                "--no-part",
                "-f",
                fmt,
                "--merge-output-format",
                "mkv",
                "--download-sections",
                f"*{start_time}-{end_time}",
                "-o",
                "-",
                url,
            ]
        )

        # Construct ffmpeg render command
        ffmpeg_cmd = [
            ffmpeg_path(),
            "-y",
            "-i",
            "pipe:0",
            "-vf",
            ",".join(vf_parts),
            "-af",
            ",".join(af_parts),
            "-c:v",
            video_codec,
        ]

        if video_codec == "libx264":
            ffmpeg_cmd.extend(["-crf", "28", "-preset", preset])
        elif video_codec == "h264_nvenc":
            ffmpeg_cmd.extend(["-rc:v", "vbr", "-cq", "18", "-preset", preset])
        else:
            ffmpeg_cmd.extend(["-preset", preset])

        ffmpeg_cmd.extend(
            [
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-use_editlist",
                "0",
                str(output_path),
            ]
        )

        log.info("Launching Stream-Piped subprocesses:")
        log.info("yt-dlp: %s", " ".join(yt_cmd))
        log.info("ffmpeg: %s", " ".join(ffmpeg_cmd))

        # Capture yt-dlp stderr so we can report meaningful downloader errors
        yt_proc = subprocess.Popen(
            yt_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=yt_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if yt_proc.stdout:
            yt_proc.stdout.close()

        try:
            ffmpeg_out, ffmpeg_errs = ffmpeg_proc.communicate(timeout=600)
            yt_out, yt_errs = yt_proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            log.error("Stream pipeline timed out! Killing yt-dlp and ffmpeg processes...")
            yt_proc.kill()
            ffmpeg_proc.kill()
            yt_proc.communicate()
            ffmpeg_proc.communicate()
            raise RuntimeError("Stream render pipeline timed out after 600 seconds.") from None

        if yt_proc.returncode != 0:
            yt_err_msg = yt_errs.decode(errors="ignore").strip()
            log.error(
                "yt-dlp download pipeline failed (exit %d): %s", yt_proc.returncode, yt_err_msg
            )
            raise RuntimeError(
                f"Stream downloader (yt-dlp) failed: {yt_err_msg or 'Unknown error'}"
            )

        if ffmpeg_proc.returncode != 0:
            ffmpeg_err_msg = ffmpeg_errs.decode(errors="ignore").strip()
            log.error(
                "FFmpeg stream rendering failed (exit %d): %s",
                ffmpeg_proc.returncode,
                ffmpeg_err_msg[-2000:],
            )
            if (
                "Invalid data found when processing input" in ffmpeg_err_msg
                or "pipe:0" in ffmpeg_err_msg
            ):
                raise RuntimeError(
                    f"FFmpeg received invalid/empty stream input. The video download stream might have failed. Error details: {ffmpeg_err_msg}"
                )
            raise RuntimeError(
                f"FFmpeg stream render failed (exit {ffmpeg_proc.returncode}): {ffmpeg_err_msg}"
            )

    log.info("✅ Pipelined stream render successfully output to %s", output_path)
    return output_path
