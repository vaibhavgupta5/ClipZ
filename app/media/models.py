"""MediaInfo: parsed ffprobe facts for one upload."""

from pydantic import BaseModel, ConfigDict, Field


class MediaInfo(BaseModel):
    """Validated container/stream facts. All rates in fps, duration in seconds."""

    model_config = ConfigDict(frozen=True)

    duration_s: float = Field(ge=0.0, description="Container duration in seconds")
    width: int = Field(gt=0, description="Coded video width")
    height: int = Field(gt=0, description="Coded video height")
    fps: float = Field(gt=0, description="Rational r_frame_rate as float")
    avg_fps: float = Field(gt=0, description="avg_frame_rate as float")
    vcodec: str = Field(description="Video codec name")
    container: str = Field(default="unknown", description="ffprobe format_name, e.g. mov,mp4,...")
    acodec: str | None = Field(default=None, description="Audio codec name (None = silent)")
    audio_channels: int = Field(default=0, ge=0, description="Audio channel count")
    sample_rate: int = Field(default=0, ge=0, description="Audio sample rate in Hz")
    rotation_deg: int = Field(default=0, description="Clockwise rotation metadata (0/90/180/270)")
    color_transfer: str = Field(default="unknown", description="Color transfer characteristic")
    size_bytes: int = Field(ge=0, description="File size in bytes")

    @property
    def is_cfr(self) -> bool:
        """True when container framerate matches the average (within 1%)."""
        return abs(self.fps - self.avg_fps) / max(self.fps, 1e-9) < 0.01

    @property
    def is_hdr(self) -> bool:
        """True for PQ/HLG transfers needing SDR tone mapping."""
        return self.color_transfer in ("smpte2084", "arib-std-b67")
