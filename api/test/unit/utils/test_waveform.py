import json
from pathlib import Path

import pook
import pytest

from api.utils.waveform import UA_STRING, download_audio, generate_waveform


_MOCK_AUDIO_PATH = Path(__file__).parent / ".." / ".." / "factory"
_MOCK_AUDIO_BYTES = (_MOCK_AUDIO_PATH / "sample-audio.mp3").read_bytes()
_MOCK_AUDIO_INFO = json.loads((_MOCK_AUDIO_PATH / "sample-audio-info.json").read_text())


@pytest.fixture
def mock_request():
    with pook.use():
        mock = (
            pook.get("http://example.org/")
            .header("User-Agent", UA_STRING)
            .reply(200)
            .headers({"Content-Type": _MOCK_AUDIO_INFO["headers"]["Content-Type"]})
            .body(_MOCK_AUDIO_BYTES)
            .mock
        )
        yield mock


def test_download_audio_sends_ua_header(mock_request):
    download_audio("http://example.org", "abcd-1234")
    # ``pook`` will only match if UA header is sent.
    assert mock_request.total_matches > 0


@pytest.mark.parametrize(
    "audio, duration",
    [
        ("sample-audio.mp3", 26000),
        ("sample-short-audio.mp3", 45),
    ],
)
def test_generate_waveform(audio, duration):
    file_name = str(_MOCK_AUDIO_PATH.joinpath(audio))

    json_out = generate_waveform(file_name, duration)
    assert len(json_out) > 0
