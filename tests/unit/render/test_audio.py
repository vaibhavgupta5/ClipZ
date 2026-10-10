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
