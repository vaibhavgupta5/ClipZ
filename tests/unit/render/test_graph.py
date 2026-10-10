"""Filtergraph builder tests: typed nodes compile to exact deterministic scripts."""

from app.render.graph import Concat, FilterGraph, OverlayAt, SetPts, Trim


def test_two_trims_plus_concat_compiles_exactly() -> None:
    g = FilterGraph(inputs=["0:v"])
    g.add(Trim("[0:v]", start=1.0, end=3.0))
    g.add(Trim("[0:v]", start=5.0, end=6.0))
    g.add(SetPts("[v1]", factor=0.5))
    g.add(Concat(("[v0]", "[v2]"), n=2))
    script, out = g.compile()
    assert script == (
        "[0:v]trim=start=1.000:end=3.000,setpts=PTS-STARTPTS[v0];"
        "[0:v]trim=start=5.000:end=6.000,setpts=PTS-STARTPTS[v1];"
        "[v1]setpts=0.5*PTS[v2];"
        "[v0][v2]concat=n=2:v=1:a=0[v3]"
    )
    assert out == "[v3]"


def test_labels_allocated_in_order() -> None:
    g = FilterGraph(inputs=["0:v", "1:v"])
    g.add(OverlayAt("[0:v]", "[1:v]", x=0, y=0, start=1.0, end=2.0))
    script, out = g.compile()
    assert script == "[0:v][1:v]overlay=0:0:enable='between(t,1.000,2.000)'[v0]"
    assert out == "[v0]"


def test_trim_rounds_to_ms() -> None:
    g = FilterGraph(inputs=["0:v"])
    g.add(Trim("[0:v]", start=0.333333, end=1.0))
    script, _ = g.compile()
    assert "start=0.333" in script


def test_zoom_punch_eases_in_holds_and_eases_out() -> None:
    from app.render.graph import ZoomPunch

    held = ZoomPunch(
        "[v]", at=1.0, dur_s=0.2, scale=1.15, width=540, height=960, hold_s=0.8
    ).render("[o]")
    assert "min(clip((t-1.000)/0.200" in held and "clip((2.000+0.250-t)/0.250" in held
    assert "*(3-2*(" in held  # smoothstep easing
    stay = ZoomPunch("[v]", at=1.0, dur_s=0.2, scale=1.15, width=540, height=960).render("[o]")
    assert "min(" not in stay  # hold_s=0 keeps zoom-and-stay
