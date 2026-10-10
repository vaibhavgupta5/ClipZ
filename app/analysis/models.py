"""AnalysisBundle: everything the agent needs to know about a video, cached by hash."""

from pydantic import BaseModel, ConfigDict, Field

from app.timeline.transcript import Beat, Transcript

Frozen = ConfigDict(frozen=True)


class SpeechRange(BaseModel):
    """VAD speech segment in source seconds."""

    model_config = Frozen

    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)


class SceneCut(BaseModel):
    """Shot boundary in source seconds."""

    model_config = Frozen

    at: float = Field(ge=0.0)


class EnergyCurve(BaseModel):
    """RMS energy per bucket (0..1), aligned to source seconds."""

    model_config = Frozen

    bucket_ms: int = Field(gt=0)
    values: list[float] = Field(description="RMS per bucket from 0.0s")


class FillerHit(BaseModel):
    """Deletable filler word candidate (context-checked)."""

    model_config = Frozen

    word_id: str = Field(description="Word ID flagged as filler")
    filler: str = Field(description="Matched filler text")


class EmphasisMark(BaseModel):
    """Emphasis score per word (prosody × LLM tags)."""

    model_config = Frozen

    word_id: str = Field(description="Emphasized word ID")
    score: float = Field(ge=0.0, le=1.0)


class FaceBox(BaseModel):
    """One face detection at time t (normalized 0..1 coords)."""

    model_config = Frozen

    t: float = Field(ge=0.0)
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(ge=0.0, le=1.0)
    h: float = Field(ge=0.0, le=1.0)


class AnalysisBundle(BaseModel):
    """Full cached analysis for one source asset."""

    model_config = Frozen

    transcript: Transcript
    speech: list[SpeechRange] = Field(default_factory=list)
    scenes: list[SceneCut] = Field(default_factory=list)
    energy: EnergyCurve = Field(default_factory=lambda: EnergyCurve(bucket_ms=100, values=[]))
    fillers: list[FillerHit] = Field(default_factory=list)
    emphasis: list[EmphasisMark] = Field(default_factory=list)
    beats: list[Beat] = Field(default_factory=list)
    faces: list[FaceBox] = Field(default_factory=list)
    language: str = Field(default="auto", description="Detected or overridden language")
    analyzer_versions: dict[str, str] = Field(default_factory=dict)


def cache_key(source_sha: str, analyzer: str, version: str, params: dict[str, object]) -> str:
    """Deterministic storage key: analysis/{sha}/{analyzer}-{version}-{params_hash}.json."""
    import hashlib
    import json

    params_hash = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]
    return f"analysis/{source_sha}/{analyzer}-{version}-{params_hash}.json"
