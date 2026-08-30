from unittest import mock

from shorts_clipper.providers.gemini import GeminiProvider, TranscriptSegment


def test_regex_fallback_with_string_floats():
    provider = GeminiProvider(api_key="dummy")
    segments = [
        TranscriptSegment(start=1.0, end=1.5, text="Hello"),
        TranscriptSegment(start=1.5, end=2.0, text="World"),
    ]

    # Mock the LLM to return INVALID JSON with string floats
    mock_response = mock.Mock()
    mock_response.text = '```json\n{"candidates": [{"start": "10.5", "end": "20.5"\n```'

    with mock.patch.object(provider, "generate_content", return_value=mock_response):
        items = provider.select_multiple_clips_detailed(segments, count=1)

        # It should extract start=10.5, end=20.5 and return a dict.
        assert len(items) == 1
        assert items[0]["start"] == 10.5
