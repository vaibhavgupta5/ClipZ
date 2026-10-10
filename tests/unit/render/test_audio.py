"""Audio chain tests: order, atempo spans, two-pass loudnorm."""

from app.render.audio import (
    atempo_chain,
    audio_filter,
    loudnorm_args,
    parse_loudnorm_json,
)


def test_chain_order_denoise_then_norm() -> None:
    filt = audio_filter([])
    assert filt.index("afftdn") < filt.index("loudnorm")
    assert "aresample=async=1" in filt


def test_atempo_chain_covers_0_25_to_4() -> None:
    assert atempo_chain(2.0) == ["atempo=2.000"]
    assert atempo_chain(0.25) == ["atempo=0.500", "atempo=0.500"]
    assert atempo_chain(4.0) == ["atempo=2.000", "atempo=2.000"]
    assert atempo_chain(1.0) == []


def test_loudnorm_two_pass_args_differ() -> None:
    measured = {
        "measured_I": "-20.0",
        "measured_TP": "-2.0",
        "measured_LRA": "3.0",
        "measured_thresh": "-30.0",
    }
    p1 = loudnorm_args(pass_n=1)
    p2 = loudnorm_args(pass_n=2, measured=measured)
    assert p1 != p2
    assert any("print_format=json" in a for a in p1)
    assert "-14" in " ".join(p2) or "I=-14" in " ".join(p2)


def test_parse_loudnorm_json() -> None:
    import json

    stderr = "...[Parsed_loudnorm_0]...\n" + json.dumps(
        {"input_i": "-20.5", "input_lra": "3.1", "input_thresh": "-30.0"}
    )
    parsed = parse_loudnorm_json(stderr)
    assert parsed["input_i"] == "-20.5"


def test_audio_filter_with_speed_spans() -> None:
    filt = audio_filter([(0.0, 1.0, 2.0)])
    assert "atrim" in filt and "atempo=2.000" in filt


def test_audio_filter_honours_denoise_and_target() -> None:
    import pytest

    plain = audio_filter([(0.0, 1.0, 1.0)], denoise=False, lufs=-16.0)
    assert "afftdn" not in plain and "I=-16.0" in plain
    assert "afftdn" not in audio_filter([], denoise=False)
    with pytest.raises(ValueError):
        audio_filter([], lufs=-3.0)


def test_audio_settings_from_timeline_fx() -> None:
    from app.render.render import audio_settings
    from app.timeline.schema import AudioEffect
    from tests.unit.timeline.test_ops import _timeline

    assert audio_settings(_timeline()) == (False, -14.0)
    tl = _timeline().model_copy(
        update={
            "audio_fx": [
                AudioEffect(type="denoise"),
                AudioEffect(type="loudnorm", params={"lufs": -16.0}),
            ]
        }
    )
    assert audio_settings(tl) == (True, -16.0)


def test_music_bed_is_ducked_mixed_then_normalised() -> None:
    import pytest

    filt = audio_filter([(0.0, 5.0, 1.0)], denoise=True, music=(-20.0, 5.0))
    assert "[1:a]aresample=48000" in filt and "volume=-20.0dB" in filt
    assert "[mus][sc]sidechaincompress" in filt and "afade=t=out:st=3.500" in filt
    assert filt.index("amix") < filt.index("loudnorm") and filt.endswith("[aout]")
    with pytest.raises(ValueError):
        audio_filter([], music=(5.0, 3.0))


def test_sfx_hits_are_delayed_and_mixed_before_loudnorm() -> None:
    filt = audio_filter(
        [(0.0, 5.0, 1.0)], music=(-20.0, 5.0), sfx=[(1.25, -12.0, 0.25), (3.0, -6.0, 0.9)]
    )
    assert "[2:a]aresample=48000" in filt and "adelay=1250|1250[s0]" in filt
    assert "atrim=end=0.250,afade=t=out:st=0.170:d=0.08" in filt  # click trimmed to 250 ms
    assert "[3:a]" in filt and "volume=-6.0dB,adelay=3000|3000[s1]" in filt
    assert "[bed][s0][s1]amix=inputs=3" in filt and filt.index("amix=inputs=3") < filt.index(
        "loudnorm"
    )
    no_music = audio_filter([], sfx=[(0.5, -10.0, 1.0)])
    assert "[1:a]aresample" in no_music and "[v0][s0]amix=inputs=2" in no_music
