"""Filler detection: per-language data files + boundary/adjacency heuristic (v1).

Precision over recall: a filler is flagged only when it starts/ends the
transcript or sits next to another filler. Internal single words ("I like pie")
are left alone — the LLM context check (Phase 5 Director) makes the final call.
"""

from pathlib import Path

from app.analysis.models import FillerHit
from app.timeline.transcript import Transcript

DATA_DIR = Path(__file__).parent / "data"


class FillerFinder:
    """Deletable filler candidates for en/hi. Version: list-1."""

    VERSION = "list-1"

    def __init__(self, language: str = "en") -> None:
        self.language = language if language in ("en", "hi", "hinglish") else "en"
        if self.language == "hinglish":  # Roman-script code-mixing: both lists apply
            merged = set(self._load("en")) | set(self._load("hi"))
            self.phrases = sorted(merged, key=len, reverse=True)
        else:
            self.phrases = self._load(self.language)

    @staticmethod
    def _load(language: str) -> list[tuple[str, ...]]:
        path = DATA_DIR / f"fillers_{language}.txt"
        phrases: list[tuple[str, ...]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip().lower()
            if line:
                phrases.append(tuple(line.split()))
        # longest phrases first so "you know" wins over "you"
        return sorted(phrases, key=len, reverse=True)

    def find(self, transcript: Transcript) -> list[FillerHit]:
        """Flag filler phrases at transcript boundaries or adjacent to fillers."""
        words = transcript.words
        if len(words) < 2:
            return []
        texts = [w.text.lower() for w in words]
        matched: set[int] = set()
        i = 0
        while i < len(words):
            for phrase in self.phrases:
                if tuple(texts[i : i + len(phrase)]) == phrase:
                    matched.update(range(i, i + len(phrase)))
                    i += len(phrase)
                    break
            else:
                i += 1
        n = len(words)
        hits = [
            FillerHit(word_id=words[i].id, filler=words[i].text)
            for i in sorted(matched)
            if i == 0 or i == n - 1 or (i - 1) in matched or (i + 1) in matched
        ]
        return hits
