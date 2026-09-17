"""Tests for batch multi-source runs (one factory run, N sources).

Covers source normalization, sequential processing, fail-fast vs
continue-on-error semantics, per-clip metrics recording, and the CLI wiring.
The full pipeline is mocked — no network access.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from shorts_clipper.core.metrics import MetricsStore
from shorts_clipper.pipeline.runner import (
    BatchResult,
    BatchRunError,
    BatchSourceResult,
    normalize_source,
    run_batch,
)


class NormalizeSourceTests(unittest.TestCase):
    def test_passes_full_urls_through(self):
        url = "https://www.youtube.com/watch?v=dq0TqEake8c"
        self.assertEqual(normalize_source(url), url)

    def test_expands_bare_video_id_to_watch_url(self):
        self.assertEqual(
            normalize_source("dq0TqEake8c"),
            "https://www.youtube.com/watch?v=dq0TqEake8c",
        )

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(normalize_source("  abc  "), "https://www.youtube.com/watch?v=abc")

    def test_empty_source_raises(self):
        with self.assertRaises(ValueError):
            normalize_source("")
        with self.assertRaises(ValueError):
            normalize_source("   ")


class RunBatchTests(unittest.TestCase):
    def _settings(self, tmp: str):
        return SimpleNamespace(
            metrics_path=Path(tmp) / "metrics.sqlite",
            channel_name="batch-test",
        )

    def test_processes_sources_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            captured = []

            def fake_run(url, **kwargs):
                captured.append(url)
                return Path("out.mp4")

            with mock.patch("shorts_clipper.pipeline.runner.run", side_effect=fake_run):
                result = run_batch(
                    [
                        "https://youtu.be/firstvideo",
                        "secondvideoid",
                        "https://www.youtube.com/watch?v=thirdvideo",
                    ],
                    settings=self._settings(tmp),
                )

        self.assertTrue(result.ok)
        self.assertEqual(result.total, 3)
        self.assertEqual(result.succeeded, 3)
        self.assertEqual(result.failed, 0)
        self.assertEqual(
            [r.source for r in result.results],
            [
                "https://youtu.be/firstvideo",
                "https://www.youtube.com/watch?v=secondvideoid",
                "https://www.youtube.com/watch?v=thirdvideo",
            ],
        )
        self.assertEqual(
            captured,
            [
                "https://youtu.be/firstvideo",
                "https://www.youtube.com/watch?v=secondvideoid",
                "https://www.youtube.com/watch?v=thirdvideo",
            ],
        )

    def test_fail_fast_stops_at_first_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fake_run(url, **kwargs):
                if url == "https://www.youtube.com/watch?v=bad":
                    raise RuntimeError("boom")
                return Path("out.mp4")

            with mock.patch("shorts_clipper.pipeline.runner.run", side_effect=fake_run) as m:
                with self.assertRaises(BatchRunError) as ctx:
                    run_batch(
                        ["ok", "bad", "never-reached"],
                        settings=self._settings(tmp),
                    )

        self.assertIn("[source 2]", str(ctx.exception))
        self.assertIn("boom", str(ctx.exception))
        self.assertEqual(m.call_count, 2)

    def test_continue_on_error_keeps_going_and_reports_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fake_run(url, **kwargs):
                if url != "https://www.youtube.com/watch?v=good":
                    raise RuntimeError("nope")
                return Path("out.mp4")

            with mock.patch("shorts_clipper.pipeline.runner.run", side_effect=fake_run) as m:
                result = run_batch(
                    ["good", "bad1", "bad2"],
                    settings=self._settings(tmp),
                    continue_on_error=True,
                )

        self.assertFalse(result.ok)
        self.assertEqual(result.succeeded, 1)
        self.assertEqual(result.failed, 2)
        self.assertEqual(result.total, 3)
        self.assertEqual(m.call_count, 3)
        failed = [r for r in result.results if not r.ok]
        self.assertEqual(len(failed), 2)
        self.assertTrue(all("nope" in (r.error or "") for r in failed))

    def test_invalid_source_is_isolated_and_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "shorts_clipper.pipeline.runner.run", return_value=Path("out.mp4")
            ) as m:
                result = run_batch(
                    ["   ", "https://youtu.be/good"],
                    settings=self._settings(tmp),
                    continue_on_error=True,
                )

        self.assertFalse(result.ok)
        self.assertEqual(result.succeeded, 1)
        self.assertEqual(result.failed, 1)
        self.assertFalse(result.results[0].ok)
        self.assertTrue(result.results[1].ok)
        self.assertEqual(m.call_count, 1)

    def test_invalid_source_fails_fast_when_not_continuing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("shorts_clipper.pipeline.runner.run") as m:
                with self.assertRaises(BatchRunError) as ctx:
                    run_batch([""], settings=self._settings(tmp))

        self.assertIn("[source 1]", str(ctx.exception))
        m.assert_not_called()

    def test_empty_batch_is_not_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("shorts_clipper.pipeline.runner.run") as m:
                result = run_batch([], settings=self._settings(tmp))
        self.assertFalse(result.ok)
        self.assertEqual(result.total, 0)
        m.assert_not_called()

    def test_batch_records_produced_clip_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            fake_out = Path(tmp) / "rendered_clip_1.mp4"
            with mock.patch(
                "shorts_clipper.pipeline.runner.run", return_value=fake_out
            ):
                result = run_batch(["abc123def45"], settings=settings)

            self.assertTrue(result.ok)
            store = MetricsStore(settings.metrics_path)
            try:
                self.assertIn("abc123def45", store.recorded_ids())
                rows = store._conn.execute(
                    "SELECT * FROM clips WHERE video_id=?", ("abc123def45",)
                ).fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["channel"], "batch-test")
            finally:
                store.close()

    def test_batch_metrics_recording_failure_does_not_fail_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = Path(tmp) / "blocked" / "sub" / "metrics.sqlite"
            Path(tmp, "blocked").write_text("i am a file, not a dir", encoding="utf-8")
            settings = SimpleNamespace(metrics_path=bad_path, channel_name="batch-test")
            with mock.patch(
                "shorts_clipper.pipeline.runner.run", return_value=Path("out.mp4")
            ):
                result = run_batch(["abc123def45"], settings=settings)

        self.assertTrue(result.ok)
        self.assertEqual(result.succeeded, 1)


class BatchResultAggregationTests(unittest.TestCase):
    def test_batch_result_exposes_totals(self):
        result = BatchResult(
            ok=False,
            results=[
                BatchSourceResult(index=1, source="a", ok=True, outputs=Path("a.mp4")),
                BatchSourceResult(index=2, source="b", ok=False, error="boom"),
            ],
            total=2,
            succeeded=1,
            failed=1,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.succeeded, 1)
        self.assertEqual(result.failed, 1)


class CliBatchWiringTests(unittest.TestCase):
    def test_resolve_sources_from_source_flag(self):
        import shorts_clipper.__main__ as cli

        args = SimpleNamespace(source=["a", "b,c"], batch_file=None, url="d")
        self.assertEqual(cli._resolve_sources(args), ["a", "b", "c"])

    def test_resolve_sources_from_batch_file(self):
        import shorts_clipper.__main__ as cli

        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp) / "sources.txt"
            batch.write_text(
                "https://youtu.be/aaa\n\n# a comment\nbbb\n",
                encoding="utf-8",
            )
            args = SimpleNamespace(
                source=None,
                batch_file=str(batch),
                url=None,
            )
            self.assertEqual(cli._resolve_sources(args), ["https://youtu.be/aaa", "bbb"])

    def test_resolve_sources_falls_back_to_positional_url(self):
        import shorts_clipper.__main__ as cli

        args = SimpleNamespace(source=None, batch_file=None, url="https://youtu.be/z")
        self.assertEqual(cli._resolve_sources(args), ["https://youtu.be/z"])

    def test_resolve_sources_returns_empty_when_none(self):
        import shorts_clipper.__main__ as cli

        args = SimpleNamespace(source=None, batch_file=None, url=None)
        self.assertEqual(cli._resolve_sources(args), [])

    def test_clip_batch_success_exits_zero(self):
        import shorts_clipper.__main__ as cli

        ok_result = BatchResult(
            ok=True,
            results=[
                BatchSourceResult(
                    index=1, source="https://youtu.be/aaa", ok=True, outputs=Path("a.mp4")
                )
            ],
            total=1,
            succeeded=1,
            failed=0,
        )
        with mock.patch(
            "shorts_clipper.pipeline.runner.run_batch", return_value=ok_result
        ):
            code = cli.main(["clip", "--source", "https://youtu.be/aaa"])
        self.assertEqual(code, 0)

    def test_clip_batch_fail_fast_exits_nonzero(self):
        import shorts_clipper.__main__ as cli

        with mock.patch(
            "shorts_clipper.pipeline.runner.run_batch",
            side_effect=BatchRunError("[source 1] https://youtu.be/aaa FAILED: boom"),
        ):
            code = cli.main(["clip", "--source", "https://youtu.be/aaa"])
        self.assertEqual(code, 1)

    def test_clip_batch_continue_on_error_still_exits_nonzero(self):
        import shorts_clipper.__main__ as cli

        failed_result = BatchResult(
            ok=False,
            results=[
                BatchSourceResult(
                    index=1, source="https://youtu.be/aaa", ok=False, error="boom"
                ),
                BatchSourceResult(
                    index=2, source="https://youtu.be/bbb", ok=True, outputs=Path("b.mp4")
                ),
            ],
            total=2,
            succeeded=1,
            failed=1,
        )
        with mock.patch(
            "shorts_clipper.pipeline.runner.run_batch", return_value=failed_result
        ):
            code = cli.main(
                [
                    "clip",
                    "--source",
                    "https://youtu.be/aaa",
                    "https://youtu.be/bbb",
                    "--continue-on-error",
                ]
            )
        self.assertEqual(code, 1)

    def test_autopilot_batch_file_success_exits_zero(self):
        import shorts_clipper.__main__ as cli

        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp) / "sources.txt"
            batch.write_text("https://youtu.be/aaa\n", encoding="utf-8")
            ok_result = BatchResult(
                ok=True,
                results=[
                    BatchSourceResult(
                        index=1,
                        source="https://youtu.be/aaa",
                        ok=True,
                        outputs=Path("a.mp4"),
                    )
                ],
                total=1,
                succeeded=1,
                failed=0,
            )
            with mock.patch(
                "shorts_clipper.pipeline.runner.run_batch", return_value=ok_result
            ):
                code = cli.main(["autopilot", "--batch-file", str(batch)])
        self.assertEqual(code, 0)

    def test_clip_without_any_source_exits_two(self):
        import shorts_clipper.__main__ as cli

        code = cli.main(["clip"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()