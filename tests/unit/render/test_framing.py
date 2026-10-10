"""Face framing: smoothed focus paths and the crop expressions they compile to."""

from app.analysis.models import FaceBox
from app.media.ffmpeg import ffmpeg_path, run_ffmpeg
from app.render.edl_to_ffmpeg import PREVIEW, build_video_filter
from app.render.framing import DEFAULT_FOCUS, crop_xy, focus_for
from app.render.graph import ReframeSplitBottom, ScaleCrop
from tests.unit.timeline.test_ops import _timeline


def _face(t: float, cx: float, cy: float, size: float = 0.2) -> FaceBox:
    return FaceBox(t=t, x=cx - size / 2, y=cy - size / 2, w=size, h=size)


def test_no_faces_biases_portrait_crops_for_head_room() -> None:
    assert focus_for([], 0.0, 10.0) == DEFAULT_FOCUS
    _, y = crop_xy(DEFAULT_FOCUS)
    assert y == "clip((0.380)*ih-0.42*oh\\,0\\,ih-oh)"  # face at 38% placed at 42% of the crop


def test_a_still_speaker_gives_one_fixed_focus_despite_jitter() -> None:
    faces = [_face(t / 2, 0.5 + (t % 2) * 0.01, 0.3) for t in range(20)]
    assert focus_for(faces, 0.0, 10.0) == ((0.0, 0.5, 0.3),)


def test_the_crop_pans_to_a_new_speaker_over_time() -> None:
    faces = [_face(t / 2, 0.3, 0.3) for t in range(0, 10)]  # 0-4.5 s: left
    faces += [_face(t / 2, 0.7, 0.5) for t in range(12, 20)]  # 6-9.5 s: right, lower
    path = focus_for(faces, 0.0, 10.0)
    assert path[0][1:] == (0.3, 0.3) and path[-1][1:] == (0.7, 0.5)
    xs = [x for _, x, _ in path]
    assert xs == sorted(xs)  # moves one way, smoothly (no back-and-forth)
    halved = focus_for(faces, 0.0, 10.0, speed=2.0)  # times are output seconds
    assert halved[-1][0] == path[-1][0] / 2
    x, _ = crop_xy(path)
    assert "clip((t-" in x and x.startswith("clip((0.300+")


def test_a_clip_without_faces_uses_the_whole_video() -> None:
    faces = [_face(t, 0.6, 0.4) for t in range(30, 40)]
    assert focus_for(faces, 0.0, 5.0) == ((0.0, 0.6, 0.4),)


def test_split_and_face_crops_use_the_focus_center_does_not() -> None:
    focus = ((0.0, 0.5, 0.3), (1.0, 0.6, 0.3))
    assert ":x=clip(" in ReframeSplitBottom("[0:v]", 540, 960, focus).render("[o]")
    assert ":x=" not in ReframeSplitBottom("[0:v]", 540, 960).render("[o]")
    assert ":y=clip(" in ScaleCrop("[0:v]", 540, 960, focus).render("[o]")
    face_tl = _timeline().model_copy(update={"reframe": "face"})
    face, _ = build_video_filter(face_tl, PREVIEW, focus=focus)
    center, _ = build_video_filter(_timeline(), PREVIEW, focus=focus)
    assert "x=clip(" in face and "x=clip(" not in center


def test_ffmpeg_accepts_the_moving_crop() -> None:
    focus = ((0.0, 0.2, 0.3), (1.0, 0.8, 0.6), (2.0, 0.5, 0.5))
    graph = ReframeSplitBottom("[0:v]", 180, 320, focus).render("[out]")
    run_ffmpeg(  # raises MediaError if the filtergraph is rejected
        [
            ffmpeg_path(),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=360x640:rate=10:duration=2",
            "-filter_complex",
            graph,
            "-map",
            "[out]",
            "-f",
            "null",
            "-",
        ]
    )
