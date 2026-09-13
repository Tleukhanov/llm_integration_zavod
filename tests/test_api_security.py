"""Security & settings-regression tests for the web API (no live HTTP)."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from shorts_clipper.api import server
from shorts_clipper.core.settings import Settings


def _load_env_text(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


class DeleteClipTraversalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / "outputs"
        self.out.mkdir(parents=True)
        self.decoy = self.root / "decoy.mp4"
        self.decoy.write_bytes(b"decoy")
        self.settings = Settings(output_dir=str(self.out))
        patcher = mock.patch.object(server.Settings, "from_env", return_value=self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_dotdot_slash_rejected_and_outside_file_untouched(self):
        with self.assertRaises(HTTPException) as ctx:
            server.delete_clip("../decoy.mp4")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertTrue(self.decoy.exists())

    def test_backslash_traversal_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            server.delete_clip("..\\decoy.mp4")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertTrue(self.decoy.exists())

    def test_normal_clip_deleted(self):
        clip = self.out / "rendered_clip_1.mp4"
        clip.write_bytes(b"clip")
        result = server.delete_clip("rendered_clip_1.mp4")
        self.assertEqual(result["status"], "deleted")
        self.assertFalse(clip.exists())

    def test_nested_clip_inside_base_is_deletable(self):
        sub = self.out / "nested"
        sub.mkdir()
        clip = sub / "rendered_clip_2.mp4"
        clip.write_bytes(b"clip")
        result = server.delete_clip("nested/rendered_clip_2.mp4")
        self.assertEqual(result["status"], "deleted")
        self.assertFalse(clip.exists())

    def test_missing_clip_returns_404(self):
        with self.assertRaises(HTTPException) as ctx:
            server.delete_clip("nonexistent.mp4")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_other_clip_routes_reject_traversal(self):
        with self.assertRaises(HTTPException) as ctx:
            server.update_clip_metadata("../decoy.mp4", server.ClipMetadataUpdate(title="hi"))
        self.assertEqual(ctx.exception.status_code, 400)
        with self.assertRaises(HTTPException) as ctx:
            server.autogen_clip_title("../decoy.mp4")
        self.assertEqual(ctx.exception.status_code, 400)


class SaveSettingsMergeTests(unittest.TestCase):
    def _chdir_tmp(self):
        self._prev_cwd = os.getcwd()
        self._tmpdir = tempfile.TemporaryDirectory()
        os.chdir(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)
        self.addCleanup(os.chdir, self._prev_cwd)

    def test_preserves_unrelated_keys_and_updates_configured_keys(self):
        self._chdir_tmp()
        Path(".env").write_text(
            "# my comment line\n"
            "GEMINI_API_KEY=old_gemini\n"
            "YOUTUBE_API_KEY=yt_key_123\n"
            "TT_CLIENT_KEY=tt_key_123\n"
            "SHORTS_SUBTITLE_STYLE=default\n",
            encoding="utf-8",
        )
        payload = server.SettingsModel(
            gemini_api_key="new_gemini",
            whisper_model="distil-large-v3",
            whisper_device="cpu",
            whisper_compute_type="int8",
            video_codec="libx264",
            video_preset="fast",
            scout_max_age_days=45,
            enable_gpu=False,
            subtitle_style="bottom_left",
        )
        server.save_settings(payload)

        raw = Path(".env").read_text(encoding="utf-8")
        env = _load_env_text(raw)
        self.assertEqual(env["YOUTUBE_API_KEY"], "yt_key_123")
        self.assertEqual(env["TT_CLIENT_KEY"], "tt_key_123")
        self.assertIn("# my comment line", raw)
        self.assertEqual(env["GEMINI_API_KEY"], "new_gemini")
        self.assertEqual(env["SHORTS_WHISPER_MODEL"], "distil-large-v3")
        self.assertEqual(env["SHORTS_WHISPER_DEVICE"], "cpu")
        self.assertEqual(env["SHORTS_WHISPER_COMPUTE_TYPE"], "int8")
        self.assertEqual(env["SHORTS_VIDEO_CODEC"], "libx264")
        self.assertEqual(env["SHORTS_VIDEO_PRESET"], "fast")
        self.assertEqual(env["SHORTS_SCOUT_MAX_AGE_DAYS"], "45")
        self.assertEqual(env["SHORTS_ENABLE_GPU"], "false")
        self.assertEqual(env["SHORTS_SUBTITLE_STYLE"], "bottom_left")

    def test_empty_gemini_key_preserves_existing_secret(self):
        self._chdir_tmp()
        Path(".env").write_text("GEMINI_API_KEY=secret_key\n", encoding="utf-8")
        payload = server.SettingsModel(whisper_model="large-v3")
        server.save_settings(payload)

        env = _load_env_text(Path(".env").read_text(encoding="utf-8"))
        self.assertEqual(env["GEMINI_API_KEY"], "secret_key")
        self.assertEqual(env["SHORTS_WHISPER_MODEL"], "large-v3")


class GetSettingsNoLeakTests(unittest.TestCase):
    def _route(self):
        for route in server.app.routes:
            if getattr(route, "path", None) == "/api/settings" and "GET" in route.methods:
                return route
        self.fail("GET /api/settings route not found")

    def test_response_excludes_key_and_exposes_flag(self):
        settings = Settings(gemini_api_key="super-secret-value")
        with mock.patch.object(server.Settings, "from_env", return_value=settings):
            resp = server.get_settings()
        self.assertIsNone(resp.gemini_api_key)
        self.assertTrue(resp.has_gemini_key)
        self.assertEqual(self._route().response_model_exclude, {"gemini_api_key"})

    def test_flag_false_without_key(self):
        settings = Settings(gemini_api_key=None)
        with mock.patch.object(server.Settings, "from_env", return_value=settings):
            resp = server.get_settings()
        self.assertFalse(resp.has_gemini_key)


class SettingsParsingTests(unittest.TestCase):
    def _from_env(self, text: str) -> Settings:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(text, encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                return Settings.from_env(env_path)

    def test_subtitle_style_read_from_env(self):
        s = self._from_env("SHORTS_SUBTITLE_STYLE=bottom_left\n")
        self.assertEqual(s.subtitle_style, "bottom_left")

    def test_subtitle_style_default(self):
        s = self._from_env("SHORTS_WHISPER_MODEL=tiny.en\n")
        self.assertEqual(s.subtitle_style, "default")

    def test_music_dir_portable_default(self):
        s = self._from_env("SHORTS_WHISPER_MODEL=tiny.en\n")
        self.assertEqual(s.music_dir, Path("data/music"))

    def test_processed_check_defaults_true(self):
        self.assertTrue(Settings().processed_check_enabled)
        s = self._from_env("SHORTS_WHISPER_MODEL=tiny.en\n")
        self.assertTrue(s.processed_check_enabled)


class PublishClipThreadsVideoIdTests(unittest.TestCase):
    """The /api/clips/{name}/publish route must resolve the source video_id
    from the sidecar metadata and pass it to engine.publish so record_publish
    can match the metrics row the factory recorded."""

    def _make_clip(self, out: Path, video_id: str) -> Path:
        clip = out / "rendered_clip_1.mp4"
        clip.write_bytes(b"clip")
        meta = {
            "video_id": video_id,
            "source_url": f"https://www.youtube.com/watch?v={video_id}",
            "title": "Title",
            "description": "Description",
            "tags": ["cs2"],
            "publish_status": "idle",
        }
        (out / "final_metadata_1.json").write_text(
            json.dumps(meta), encoding="utf-8"
        )
        return clip

    def test_publish_route_passes_video_id_from_sidecar(self):
        from fastapi import BackgroundTasks

        from shorts_clipper.core.settings import Settings as CoreSettings
        from shorts_clipper.publishers.models import PublishResult

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "outputs"
            out.mkdir()
            clip = self._make_clip(out, "vid42")
            self.assertTrue(clip.exists())

            settings = CoreSettings(output_dir=str(out), publish_platforms=["youtube"])
            with mock.patch.object(server.Settings, "from_env", return_value=settings), \
                 mock.patch("shorts_clipper.publishers.manager.PublishingEngine") as mock_engine:
                mock_engine.return_value.publish.return_value = {
                    "youtube": PublishResult(
                        "youtube",
                        True,
                        "https://youtube.com/shorts/vid42",
                        "vid42",
                        "2026-01-01T00:00:00Z",
                    )
                }
                tasks = BackgroundTasks()
                resp = server.publish_clip("rendered_clip_1.mp4", tasks)
                self.assertEqual(resp["status"], "started")
                asyncio.run(tasks())

        mock_engine.return_value.publish.assert_called_once()
        self.assertEqual(
            mock_engine.return_value.publish.call_args.kwargs["video_id"], "vid42"
        )


class WhisperLanguageTests(unittest.TestCase):
    def test_auto_and_empty_map_to_none(self):
        from shorts_clipper.transcription.whisper import _normalize_language

        self.assertIsNone(_normalize_language("auto"))
        self.assertIsNone(_normalize_language(""))
        self.assertIsNone(_normalize_language(None))
        self.assertIsNone(_normalize_language("  "))

    def test_explicit_language_preserved(self):
        from shorts_clipper.transcription.whisper import _normalize_language

        self.assertEqual(_normalize_language("ru"), "ru")
        self.assertEqual(_normalize_language(" en "), "en")

    def test_models_dir_passed_as_download_root(self):
        import sys
        import types

        import shorts_clipper.transcription.whisper as whisper_mod

        fake_module = types.ModuleType("faster_whisper")
        fake_model_call = mock.MagicMock(return_value=object())
        fake_module.WhisperModel = fake_model_call

        with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}), mock.patch.object(
            whisper_mod, "_global_model", None
        ), mock.patch.object(
            Settings,
            "from_env",
            return_value=Settings(
                models_dir=Path("data/models"),
                whisper_device="cpu",
                whisper_compute_type="int8",
            ),
        ):
            whisper_mod.get_whisper_model()
        fake_model_call.assert_called_once()
        self.assertEqual(
            fake_model_call.call_args.kwargs["download_root"], str(Path("data/models"))
        )


if __name__ == "__main__":
    unittest.main()