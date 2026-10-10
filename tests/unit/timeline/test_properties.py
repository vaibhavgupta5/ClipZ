"""Property tests: snapping monotonicity, no mid-word cuts, ops preserve validity."""

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.timeline import ops, snap
from app.timeline.errors import OpError
from app.timeline.schema import CaptionConfig, OutputSpec, Timeline, VideoClip
from app.timeline.transcript import Transcript, Word
from app.timeline.validate import ensure_valid

MAX_EXAMPLES = 50


def _timeline(n_words: int) -> Timeline:
    words = [
        Word(
            id=f"w{i + 1}",
            start=i * 0.5,
            end=i * 0.5 + 0.4,
            text="x",
            display_text="x",
            lang="en",
        )
        for i in range(n_words)
    ]
    return Timeline(
        project_id="00000000-0000-0000-0000-000000000000",
        transcript=Transcript(words=words),
        video_clips=[VideoClip(id="c1", asset="a1", src_in=0.0, src_out=n_words * 0.5)],
        captions=CaptionConfig(),
        output=OutputSpec(),
    )


word_counts = st.integers(min_value=2, max_value=12)


@given(word_counts)
@settings(max_examples=MAX_EXAMPLES, suppress_health_check=[HealthCheck.too_slow])
def test_output_times_increase_with_word_order(n: int) -> None:
    tl = _timeline(n)
    times = [snap.output_time_of(tl, f"w{i + 1}") for i in range(n)]
    assert all(b > a for a, b in zip(times, times[1:], strict=False))


@given(word_counts, st.data())
@settings(max_examples=MAX_EXAMPLES, suppress_health_check=[HealthCheck.too_slow])
def test_boundaries_never_split_words(n: int, data: st.DataObject) -> None:
    tl = _timeline(n)
    ids = [f"w{i + 1}" for i in range(n)]
    for _ in range(data.draw(st.integers(min_value=1, max_value=4))):
        if not ids:
            break  # everything deleted; further ops are meaningless
        choice = data.draw(st.sampled_from(["del", "trim", "split", "speed"]))
        try:
            if choice == "del":
                victim = data.draw(st.sampled_from(ids))
                tl, _ = ops.delete_words(tl, [victim])
                ids.remove(victim)
            elif choice == "trim" and len(ids) >= 2:
                tl, _ = ops.trim(tl, ids[0], ids[-1])
            elif choice == "split" and len(ids) >= 2:
                tl, _ = ops.split(tl, data.draw(st.sampled_from(ids[1:])))
            else:
                tl, _ = ops.set_speed(tl, ids[0], ids[-1], 1.5)
        except OpError:
            pass  # some random op sequences are invalid by design; skip them
    for clip in tl.video_clips:
        for bound in (clip.src_in, clip.src_out):
            for w in tl.transcript.words:
                assert not (w.start + 1e-9 < bound < w.end - 1e-9), f"{bound} splits {w.id}"


@given(word_counts, st.data())
@settings(max_examples=MAX_EXAMPLES, suppress_health_check=[HealthCheck.too_slow])
def test_ops_preserve_validity(n: int, data: st.DataObject) -> None:
    tl = _timeline(n)
    ensure_valid(tl)
    victim = data.draw(st.sampled_from([f"w{i + 1}" for i in range(n)]))
    try:
        tl, _ = ops.delete_words(tl, [victim])
    except OpError:
        return  # deleting the only content is rejected, not invalid
    ensure_valid(tl)  # must not raise
