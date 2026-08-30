from shorts_clipper.core.models import TranscriptSegment, TranscriptWord
from shorts_clipper.editorial.engine import EditorialEngine
from shorts_clipper.editorial.feature_store import FeatureStore
from shorts_clipper.editorial.plugins.hook import HookJudge
from shorts_clipper.editorial.plugins.silence import SilenceJudge


def test_feature_store_computation():
    segments = [
        TranscriptSegment(
            start=0.0,
            end=2.0,
            text="This is a test.",
            words=[
                TranscriptWord(start=0.0, end=0.5, word="This"),
                TranscriptWord(start=0.5, end=1.0, word="is"),
                TranscriptWord(start=1.0, end=1.5, word="a"),
                TranscriptWord(start=1.5, end=2.0, word="test."),
            ],
        ),
        TranscriptSegment(
            start=5.0,
            end=7.0,
            text="There was a long pause.",
            words=[
                TranscriptWord(start=5.0, end=5.4, word="There"),
                TranscriptWord(start=5.4, end=5.8, word="was"),
                TranscriptWord(start=5.8, end=6.2, word="a"),
                TranscriptWord(start=6.2, end=6.6, word="long"),
                TranscriptWord(start=6.6, end=7.0, word="pause."),
            ],
        ),
    ]
    features = FeatureStore.compute(segments)

    assert features.total_duration == 7.0
    assert features.word_count == 9
    assert features.longest_pause == 3.0  # 5.0 - 2.0
    assert features.ends_with_punctuation is True
    assert features.has_hanging_pronoun is False


def test_hook_judge():
    judge = HookJudge()

    # Strong hook
    features_strong = FeatureStore.compute(
        [
            TranscriptSegment(
                start=0.0,
                end=2.0,
                text="Wait! You won't believe this secret.",
                words=[
                    TranscriptWord(start=0.0, end=0.3, word="Wait!"),
                    TranscriptWord(start=0.3, end=0.6, word="You"),
                    TranscriptWord(start=0.6, end=1.0, word="won't"),
                    TranscriptWord(start=1.0, end=1.3, word="believe"),
                    TranscriptWord(start=1.3, end=1.6, word="this"),
                    TranscriptWord(start=1.6, end=2.0, word="secret."),
                ],
            )
        ]
    )
    result_strong = judge.evaluate(features_strong)
    assert result_strong.score > 50.0

    # Weak hook
    features_weak = FeatureStore.compute(
        [
            TranscriptSegment(
                start=0.0,
                end=2.0,
                text="Hello everybody, today we talk about trees.",
                words=[
                    TranscriptWord(start=0.0, end=0.3, word="Hello"),
                    TranscriptWord(start=0.3, end=0.6, word="everybody,"),
                    TranscriptWord(start=0.6, end=1.0, word="today"),
                    TranscriptWord(start=1.0, end=1.3, word="we"),
                    TranscriptWord(start=1.3, end=1.6, word="talk"),
                    TranscriptWord(start=1.6, end=1.8, word="about"),
                    TranscriptWord(start=1.8, end=2.0, word="trees."),
                ],
            )
        ]
    )
    result_weak = judge.evaluate(features_weak)
    assert result_weak.score == 20.0


def test_silence_judge():
    judge = SilenceJudge()

    features_bad = FeatureStore.compute(
        [
            TranscriptSegment(
                start=0.0,
                end=2.0,
                text="Hello.",
                words=[TranscriptWord(start=0.0, end=2.0, word="Hello.")],
            ),
            TranscriptSegment(
                start=6.0,
                end=8.0,
                text="Wow.",
                words=[TranscriptWord(start=6.0, end=8.0, word="Wow.")],
            ),
        ]
    )
    result = judge.evaluate(features_bad)
    assert result.reject_hard is True


def test_editorial_engine_selection():
    engine = EditorialEngine()

    segments = []
    # Create 90 seconds of dummy transcript
    for i in range(30):
        start = i * 3.0
        segments.append(
            TranscriptSegment(
                start=start,
                end=start + 2.5,
                text=f"Sentence number {i}.",
                words=[
                    TranscriptWord(start=start, end=start + 0.8, word="Sentence"),
                    TranscriptWord(start=start + 0.8, end=start + 1.6, word="number"),
                    TranscriptWord(start=start + 1.6, end=start + 2.5, word=f"{i}."),
                ],
            )
        )

    # Inject a strong hook at second 30
    segments[10] = TranscriptSegment(
        start=30.0,
        end=32.5,
        text="Wait! You won't believe this secret!",
        words=[
            TranscriptWord(start=30.0, end=30.4, word="Wait!"),
            TranscriptWord(start=30.4, end=30.8, word="You"),
            TranscriptWord(start=30.8, end=31.2, word="won't"),
            TranscriptWord(start=31.2, end=31.6, word="believe"),
            TranscriptWord(start=31.6, end=32.0, word="this"),
            TranscriptWord(start=32.0, end=32.5, word="secret!"),
        ],
    )

    clips = engine.select_clips(segments, count=1, window_duration=20.0, step=5.0)

    assert len(clips) == 1
    best_clip = clips[0]
    # Should pick the window starting around 30.0 due to the strong hook
    assert 28.0 <= best_clip.start <= 32.0
