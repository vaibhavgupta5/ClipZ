"""RMS energy curve from decoded PCM (reuses the waveform peak math)."""

from app.analysis.models import EnergyCurve
from app.media.ffmpeg import ffmpeg_path, run_capture


class EnergyAnalyzer:
    """Loudness curve for emphasis + ducking. Version: rms-1."""

    VERSION = "rms-1"
    BUCKET_MS = 100

    def curve(self, wav_path: str) -> EnergyCurve:
        """RMS per 100 ms bucket over mono 16 kHz decode."""
        from app.media.waveform import peaks_from_pcm

        pcm = run_capture(
            [
                ffmpeg_path(),
                "-v",
                "error",
                "-i",
                wav_path,
                "-ar",
                "16000",
                "-ac",
                "1",
                "-f",
                "f32le",
                "-",
            ]
        )
        return EnergyCurve(
            bucket_ms=self.BUCKET_MS, values=peaks_from_pcm(pcm, 16000, self.BUCKET_MS)
        )
