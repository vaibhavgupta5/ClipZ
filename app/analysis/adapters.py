"""Model unavailability error with install hints for the GPU track."""


class ModelUnavailable(RuntimeError):
    """A heavy model dependency is not installed. Carries the pip spec to fix it."""

    def __init__(self, dependency: str, pip_spec: str) -> None:
        self.dependency = dependency
        self.pip_spec = pip_spec
        super().__init__(f"model dependency {dependency!r} missing: pip install {pip_spec}")
