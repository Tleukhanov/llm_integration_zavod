"""Junction tests: auto_discover output -> factory recording -> metrics store.

This closes the W2-3 gap: the source channel discovered by the scout must
survive into the ``clips`` row's ``source_channel`` column, and that row must
be keyed by the same ``video_id`` the publisher later confirms, so the
metrics -> scoring feedback loop is not starved. No network: the scout is
mocked and the metrics DB is a throwaway sqlite file.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from shorts_clipper.scout.auto_batch import auto_discover
from shorts_clipper.scout.source_scout import SourceVideo


def _load_factory_module():
    import importlib.util

    repo_root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "factory_script", repo_root / "scripts" / "factory.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class AutoDiscoverToFactoryMetricsTests(unittest.TestCase):
    def test_source_channel_reaches_metrics_via_factory_recording(self):
        from shorts_clipper.core.metrics import MetricsStore

        factory = _load_factory_module()
        videos = [
            SourceVideo(
                url="https://www.youtube.com/watch?v=vod_cc",
                video_id="vod_cc",
                title="Some VOD",
                platform="youtube",
                extra={"channel": "ИмяКаналаYT", "view_count": 1000},
            )
        ]
        with tempfile.TemporaryDirectory() as tmp:
            settings = SimpleNamespace(
                processed_videos_path=str(Path(tmp) / "processed.json"),
                metrics_path=str(Path(tmp) / "metrics.sqlite"),
                hook_banner_enabled=False,
                hook_banner_text="",
            )
            with mock.patch("shorts_clipper.scout.auto_batch.scout", return_value=videos):
                discovered = auto_discover(
                    settings, query="cs2", providers=("youtube",), max_results=10
                )

            self.assertEqual(len(discovered), 1)
            self.assertEqual(discovered[0]["channel"], "ИмяКаналаYT")

            store = MetricsStore(Path(settings.metrics_path))
            try:
                factory._record_produced_clip(
                    settings,
                    store,
                    url=discovered[0]["url"],
                    video=discovered[0],
                    output_path=Path(tmp) / "rendered_clip_1.mp4",
                    channel="профиль",
                    published=False,
                )
                row = store._conn.execute(
                    "SELECT * FROM clips WHERE video_id=?", ("vod_cc",)
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["video_id"], "vod_cc")
                self.assertEqual(row["channel"], "профиль")
                self.assertEqual(row["source_channel"], "ИмяКаналаYT")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()