import glob
import json
import logging

from shorts_clipper.metadata.fallback import generate_fallback_metadata

log = logging.getLogger(__name__)


class SegmentWrapper:
    def __init__(self, data):
        self.start = data.get("start", 0)
        self.end = data.get("end", 0)
        self.text = data.get("text", "")


def run_repair():
    log.info("Scanning library for metadata missing clips...")
    files = glob.glob("outputs/clip_*.json")
    repaired_count = 0

    for f in files:
        try:
            with open(f, encoding="utf-8") as file:
                data = json.load(file)

            title = data.get("title")
            desc = data.get("description")

            if not title or not desc:
                log.info(f"Repairing {f}...")
                segments_data = data.get("segments", [])
                segments = [SegmentWrapper(s) for s in segments_data]

                # Fetch source video title/channel if available from cache?
                # For repair, we might not know the original video ID easily unless it's in the json.
                # Just use default empty strings, fallback generator will use what it can.
                fallback = generate_fallback_metadata(
                    segments=segments, source_title="", source_channel="", niche="tech"
                )

                data["title"] = fallback["title"]
                data["description"] = fallback["description"]
                data["tags"] = fallback["tags"]

                # Remove publish block state if we just fixed it
                if data.get("publish_status") == "failed" and "metadata" in (
                    data.get("publish_error") or ""
                ):
                    data["publish_status"] = "idle"
                    data["publish_error"] = None

                with open(f, "w", encoding="utf-8") as out:
                    json.dump(data, out, indent=2, ensure_ascii=False)

                repaired_count += 1
                log.info(f"Successfully repaired {f}")
        except Exception as e:
            log.error(f"Error repairing {f}: {e}")

    print(f"Repaired {repaired_count} clips.")
    return 0
