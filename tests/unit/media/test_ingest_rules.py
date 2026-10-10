"""Ingest gate tests: caps, HDR/rotation/VFR arg builders (no subprocess)."""

import pytest

from app.media.errors import IngestError
from app.media.models import MediaInfo
from app.media.normalize import asr_audio_args, normalize_args, proxy_args
from app.media.validate import MAX_UPLOAD_BYTES, validate_info


def _info(**overrides):  # type: ignore[no-untyped-def]
    base = dict(
        duration_s=30.0,
        width=720,
        height=1280,
        fps=30.0,
        avg_fps=30.0,
        vcodec="h264",
        acodec="aac",
        audio_channels=2,
        sample_rate=48000,
        rotation_deg=0,
        color_transfer="bt709",
        container="mov,mp4,m4a,3gp,3g2,mj2",
        size_bytes=10_000_000,
    )
    base.update(overrides)
    return MediaInfo(**base)


def test_89s_ok_91s_rejected() -> None:
    validate_info(_info(duration_s=89.0))  # must not raise
    validate_info(_info(duration_s=90.0))
    with pytest.raises(IngestError, match="too_long"):
        validate_info(_info(duration_s=90.001))


def test_caps_rejected() -> None:
    with pytest.raises(IngestError, match="too_large"):
        validate_info(_info(size_bytes=MAX_UPLOAD_BYTES + 1))
    with pytest.raises(IngestError, match="resolution"):
        validate_info(_info(width=7680, height=4320))
    with pytest.raises(IngestError, match="frame_rate"):
        validate_info(_info(fps=240.0, avg_fps=240.0))
    with pytest.raises(IngestError, match="container"):
        validate_info(_info(container="hls"))
    with pytest.raises(IngestError, match="container"):
        validate_info(_info(container="unknown"))


def test_silent_accepted_with_note() -> None:
    from app.media.validate import warnings

    assert warnings(_info(acodec=None, audio_channels=0, sample_rate=0)) == ["silent"]
    assert warnings(_info()) == []


def test_normalize_forces_cfr_and_applies_rotation_once() -> None:
    args = normalize_args("/in.mp4", "/out.mp4", _info(fps=60.0, avg_fps=21.4, rotation_deg=90))
    vf = args[args.index("-vf") + 1]
    assert "fps=30" in vf
    assert "transpose=1" in vf  # 90° CW
    assert vf.count("transpose") == 1


def test_normalize_hdr_tone_maps_and_sdr_skips() -> None:
    hdr = normalize_args("/in", "/out", _info(color_transfer="smpte2084"))
    assert "tonemap=hable" in hdr[hdr.index("-vf") + 1]
    sdr = normalize_args("/in", "/out", _info())
    assert "tonemap" not in sdr[sdr.index("-vf") + 1]


def test_proxy_is_540p_short_gop() -> None:
    args = proxy_args("/in.mp4", "/out.mp4")
    vf = args[args.index("-vf") + 1]
    assert "scale=540:-2" in vf
    assert args[args.index("-g") + 1] == "15"
    assert "-movflags" in args and "+faststart" in args


def test_asr_audio_is_16k_mono() -> None:
    args = asr_audio_args("/in.mp4", "/out.wav")
    assert args[args.index("-ar") + 1] == "16000"
    assert args[args.index("-ac") + 1] == "1"
