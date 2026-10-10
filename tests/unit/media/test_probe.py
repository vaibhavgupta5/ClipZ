"""Probe tests: ffprobe JSON -> MediaInfo, incl. VFR, rotation, HDR, audio-less."""

import pytest

from app.media.errors import MediaError
from app.media.probe import parse_probe_json


def _video(**overrides):  # type: ignore[no-untyped-def]
    base = {
        "codec_type": "video",
        "codec_name": "h264",
        "width": 720,
        "height": 1280,
        "r_frame_rate": "30/1",
        "avg_frame_rate": "30/1",
        "duration": "5.000000",
        "nb_frames": "150",
    }
    base.update(overrides)
    return base


def _audio(**overrides):  # type: ignore[no-untyped-def]
    base = {
        "codec_type": "audio",
        "codec_name": "aac",
        "channels": 2,
        "sample_rate": "48000",
    }
    base.update(overrides)
    return base


def _doc(*streams):  # type: ignore[no-untyped-def]
    return {"streams": list(streams), "format": {"duration": "5.000000", "size": "1000"}}


def test_cfr_portrait_parses() -> None:
    info = parse_probe_json(_doc(_video(), _audio()), size_bytes=1000)
    assert (info.width, info.height) == (720, 1280)
    assert info.fps == pytest.approx(30.0)
    assert info.is_cfr
    assert info.audio_channels == 2
    assert info.rotation_deg == 0


def test_vfr_detected_from_frame_rate_mismatch() -> None:
    info = parse_probe_json(_doc(_video(avg_frame_rate="150/7")), size_bytes=1000)
    assert not info.is_cfr


def test_rotation_tag_and_displaymatrix() -> None:
    tagged = _video(tags={"rotate": "90"})
    assert parse_probe_json(_doc(tagged), size_bytes=1).rotation_deg == 90
    matrix = _video(side_data_list=[{"side_data_type": "Display Matrix", "rotation": -90}])
    assert parse_probe_json(_doc(matrix), size_bytes=1).rotation_deg == 270  # -90 CCW = 270 CW


def test_hdr_transfer_flagged() -> None:
    info = parse_probe_json(_doc(_video(color_transfer="smpte2084")), size_bytes=1)
    assert info.is_hdr


def test_silent_video_has_no_audio() -> None:
    info = parse_probe_json(_doc(_video()), size_bytes=1)
    assert info.audio_channels == 0
    assert info.sample_rate == 0


def test_no_video_stream_rejected() -> None:
    with pytest.raises(MediaError, match="no_video"):
        parse_probe_json(_doc(_audio()), size_bytes=1)


def test_garbage_json_rejected() -> None:
    with pytest.raises(MediaError, match="unparseable"):
        parse_probe_json({"streams": [{"codec_type": "video"}]}, size_bytes=1)  # no dims
