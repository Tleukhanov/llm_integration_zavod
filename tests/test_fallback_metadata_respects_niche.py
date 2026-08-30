from pathlib import Path
from unittest import mock

from shorts_clipper.core.models import ClipWindow
from shorts_clipper.core.settings import Settings
from shorts_clipper.pipeline.runner import run


@mock.patch("shorts_clipper.pipeline.runner.fetch_subtitles")
@mock.patch("shorts_clipper.pipeline.runner.GeminiProvider")
@mock.patch("shorts_clipper.pipeline.runner.download_audio")
@mock.patch("shorts_clipper.pipeline.runner.transcribe_clip")
@mock.patch("shorts_clipper.pipeline.runner.download_clip")
@mock.patch("shorts_clipper.pipeline.runner.process_to_vertical")
@mock.patch("shorts_clipper.pipeline.runner.burn_subtitles")
@mock.patch("shorts_clipper.metadata.fallback.generate_fallback_metadata")
def test_fallback_metadata_respects_niche(
    mock_fallback,
    mock_burn,
    mock_process,
    mock_download_clip,
    mock_transcribe,
    mock_download_audio,
    mock_provider_cls,
    mock_fetch_subs,
    tmp_path,
):
    settings = Settings(gemini_api_key="dummy", youtube_api_key="dummy")
    preselected = [(ClipWindow(start=10.0, end=20.0), "crop_center")]

    mock_fetch_subs.return_value = [{"start": 0.0, "end": 100.0, "text": "Dummy text"}]

    # Force Gemini metadata generation to fail so we use the fallback
    mock_provider_instance = mock.Mock()
    mock_provider_instance.generate_clip_metadata.side_effect = Exception("Forced Gemini Error")
    mock_provider_cls.return_value = mock_provider_instance

    mock_transcribe.return_value = []
    mock_process.return_value = tmp_path / "cropped.mp4"
    mock_burn.return_value = tmp_path / "final.mp4"
    mock_fallback.return_value = {
        "title": "Fallback Title",
        "description": "Fallback Desc",
        "tags": ["finance"],
    }

    with mock.patch("shorts_clipper.pipeline.runner.EditorialFinisher") as MockFinisher:
        mock_finisher_inst = MockFinisher.return_value
        mock_finisher_inst.snap_boundaries.side_effect = lambda *args, **kwargs: ClipWindow(
            start=args[0], end=args[1]
        )

        result = run(
            "https://youtube.com/watch?v=dummy",
            settings=settings,
            count=1,
            upload=False,
            preselected_clips=preselected,
            niche="finance",
        )

        assert isinstance(result, Path)
        mock_fallback.assert_called_once()
        _, kwargs = mock_fallback.call_args
        assert kwargs.get("niche") == "finance"
