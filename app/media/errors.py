"""Media pipeline errors: ffprobe/ffmpeg failures and ingest rejections."""


class MediaError(RuntimeError):
    """ffprobe/ffmpeg invocation or output-parse failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"[{code}] {message}")


class IngestError(ValueError):
    """Upload rejected at the ingest gate; message is user-facing."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        self.code = code
        self.hint = hint
        super().__init__(f"[{code}] {message}" + (f" Hint: {hint}" if hint else ""))
