"""Crop focus: follow the speaker's face so split view and face framing never cut the head.

A focus is a path of keyframes (t, x, y): t in clip-relative OUTPUT seconds, (x, y) the face
centre in normalized source coordinates. The crop eases linearly from one keyframe to the
next (1 s apart), so the frame pans smoothly when the speaker moves or another person talks.
The crop places the face horizontally centred and slightly above the middle (head room).

Without face boxes a portrait source still gets a head-room bias: in a phone selfie the face
sits around 38% of the frame height, so a dead-centre crop cuts the top of the head.
"""

from statistics import median

from app.analysis.models import FaceBox

Focus = tuple[tuple[float, float, float], ...]  # (t_out_rel, x, y) keyframes

DEFAULT_XY = (0.5, 0.38)
DEFAULT_FOCUS: Focus = ((0.0, *DEFAULT_XY),)
PLACE_Y = 0.42  # the face centre lands at 42% of the crop height
KEY_S = 1.0  # source seconds between keyframes (= pan time to a new position)
WINDOW_S = 1.0  # detections within this distance of a keyframe vote for it
DEAD_ZONE = 0.04  # moves smaller than this are jitter: hold still
MIN_BOXES = 2  # fewer detections than this in the whole video: use the default
STEP = 0.02  # positions are rounded so tiny jitter never invalidates cached segments


def _snap(v: float) -> float:
    return round(round(v / STEP) * STEP, 2)


def _centre(boxes: list[FaceBox]) -> tuple[float, float]:
    return median(b.x + b.w / 2 for b in boxes), median(b.y + b.h / 2 for b in boxes)


def focus_for(faces: list[FaceBox], src_in: float, src_out: float, speed: float = 1.0) -> Focus:
    """Smoothed keyframe path of the face across a clip's source range.

    Keyframes with no face nearby hold the previous position (the first found one before
    any detection); a clip with no faces at all uses the whole video's median, else default.
    """
    if len(faces) < MIN_BOXES:
        return DEFAULT_FOCUS
    keys: list[float] = []
    t = src_in
    while t < src_out - 1e-6:
        keys.append(t)
        t += KEY_S
    keys.append(src_out)
    raw = [_raw(faces, k) for k in keys]
    found = [p for p in raw if p is not None]
    if not found:
        whole = _centre(faces)
        return ((0.0, _snap(whole[0]), _snap(whole[1])),)
    filled: list[tuple[float, float]] = []
    last = found[0]
    for p in raw:
        last = p if p is not None else last
        filled.append(last)
    smooth = [_mean(filled[max(0, i - 1) : i + 2]) for i in range(len(filled))]  # 3-tap
    path: list[tuple[float, float, float]] = []
    for k, (x, y) in zip(keys, smooth, strict=True):
        if path and abs(x - path[-1][1]) < DEAD_ZONE and abs(y - path[-1][2]) < DEAD_ZONE:
            x, y = path[-1][1], path[-1][2]  # hold still through jitter
        path.append((round((k - src_in) / speed, 3), _snap(x), _snap(y)))
    if all(p[1:] == path[0][1:] for p in path):
        return ((0.0, *path[0][1:]),)
    return tuple(path)


def _mean(points: list[tuple[float, float]]) -> tuple[float, float]:
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


def _raw(faces: list[FaceBox], key: float) -> tuple[float, float] | None:
    near = [b for b in faces if abs(b.t - key) <= WINDOW_S]
    return _centre(near) if near else None


def _track(focus: Focus, axis: int) -> str:
    """Expression for one coordinate over time: the first value plus a linear ramp per move."""
    terms = [f"{focus[0][axis]:.3f}"]
    for (t0, *a), (t1, *b) in zip(focus, focus[1:], strict=False):
        delta = b[axis - 1] - a[axis - 1]
        if abs(delta) > 1e-9 and t1 > t0:
            terms.append(f"{delta:+.3f}*clip((t-{t0:.3f})/{t1 - t0:.3f}\\,0\\,1)")
    return "(" + "".join(terms) + ")"


def crop_xy(focus: Focus) -> tuple[str, str]:
    """FFmpeg crop x/y expressions putting the focus at (centre, PLACE_Y), kept in frame.

    Only typed floats enter the expression; commas are escaped for the filtergraph.
    """
    clamped: Focus = tuple((t, min(1.0, max(0.0, x)), min(1.0, max(0.0, y))) for t, x, y in focus)
    x = f"clip({_track(clamped, 1)}*iw-ow/2\\,0\\,iw-ow)"
    y = f"clip({_track(clamped, 2)}*ih-{PLACE_Y:.2f}*oh\\,0\\,ih-oh)"
    return x, y
