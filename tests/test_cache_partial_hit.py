from unittest import mock

from shorts_clipper.core.models import ClipWindow
from shorts_clipper.core.settings import Settings
from shorts_clipper.pipeline.runner import run


@mock.patch("shorts_clipper.pipeline.runner.fetch_subtitles")
@mock.patch("shorts_clipper.highlight_detection.scoring.SemanticCandidateGenerator")
@mock.patch("shorts_clipper.attention.engine.SimulationEngine")
@mock.patch("shorts_clipper.pipeline.runner.download_audio")
@mock.patch("shorts_clipper.pipeline.runner.transcribe_clip")
@mock.patch("shorts_clipper.pipeline.runner.download_clip")
@mock.patch("shorts_clipper.pipeline.runner.process_to_vertical")
@mock.patch("shorts_clipper.pipeline.runner.burn_subtitles")
def test_preselected_partial_hit_behavior(
    mock_burn,
    mock_process,
    mock_download_clip,
    mock_transcribe,
    mock_download_audio,
    mock_sim_cls,
    mock_scorer_cls,
    mock_fetch_subs,
    tmp_path,
):
    from shorts_clipper.core.models import TranscriptSegment

    settings = Settings(gemini_api_key="dummy", youtube_api_key="dummy")

    preselected = [
        (ClipWindow(start=10.0, end=20.0), "crop_center"),
        (ClipWindow(start=30.0, end=40.0), "split_screen"),
    ]

    mock_fetch_subs.return_value = [
        TranscriptSegment(start=0.0, end=100.0, text="Dummy text", words=[])
    ]

    mock_scorer = mock.Mock()
    mock_scorer.generate_candidate.return_value = (
        90.0,
        [TranscriptSegment(start=50.0, end=60.0, text="Dummy", words=[])],
        "reason",
    )
    mock_scorer_cls.return_value = mock_scorer

    mock_sim = mock.Mock()

    class FakeReport:
        completion_prob = 0.85
        scroll_stop_prob = 0.75
        payoff_strength = 0.90
        overall_confidence = 0.80
        judge_results = {}

    class FakeResult:
        winner_id = "base"
        runner_up_id = "none"
        improvement_percentage = 10.0
        reason = "test reasoning"
        base_variant = mock.Mock(start_time=50.0, end_time=60.0)
        variants = [mock.Mock(variant_id="base", start_time=50.0, end_time=60.0)]
        reports = {"base": FakeReport()}

    mock_sim_result = FakeResult()
    mock_sim.optimize_clip.return_value = mock_sim_result
    mock_sim_cls.return_value = mock_sim

    mock_transcribe.return_value = []

    mock_process.return_value = tmp_path / "cropped.mp4"
    mock_burn.return_value = tmp_path / "final.mp4"

    with mock.patch("shorts_clipper.pipeline.runner.EditorialFinisher") as MockFinisher:
        mock_finisher_inst = MockFinisher.return_value
        mock_finisher_inst.snap_boundaries.side_effect = lambda *args, **kwargs: ClipWindow(
            start=args[0], end=args[1]
        )

        result = run(
            "https://youtube.com/watch?v=dummy",
            settings=settings,
            count=3,
            upload=False,
            preselected_clips=preselected,
        )

        assert len(result) == 3

        mock_scorer.generate_candidate.assert_called_once()
