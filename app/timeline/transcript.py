"""Transcript model: stable word IDs + semantic beats. Timings are source seconds."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Lang = Literal["en", "hi"]
Intent = Literal["illustrate", "react", "joke", "emphasize", "proof", "transition"]


class Word(BaseModel):
    """One ASR word with stable ID. Never cut inside [start, end]."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Stable word ID, w1, w2, ...", pattern=r"^w\d+$")
    start: float = Field(ge=0.0, description="Word start in source seconds")
    end: float = Field(ge=0.0, description="Word end in source seconds")
    text: str = Field(min_length=1, description="Canonical word text")
    display_text: str = Field(
        min_length=1, description="Caption text (follows caption_script policy)"
    )
    lang: Lang = Field(description="Word language")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="ASR confidence")

    @model_validator(mode="after")
    def _end_after_start(self) -> "Word":
        if self.end <= self.start:
            raise ValueError(f"word {self.id}: end ({self.end}) must be after start ({self.start})")
        return self


class Beat(BaseModel):
    """Semantic moment: word range + intent + retrieval hint for the Insert Engine."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Stable beat ID, b1, b2, ...", pattern=r"^b\d+$")
    word_ids: list[str] = Field(min_length=1, description="Covered word IDs in order")
    intent: Intent = Field(description="Insert intent")
    query_hint: str = Field(default="", description="Short asset search hint")
    tone: str = Field(default="", description="Tone tag: sarcastic, shocked, celebratory...")


class Transcript(BaseModel):
    """Canonical word list + beats for one asset."""

    model_config = ConfigDict(frozen=True)

    words: list[Word] = Field(description="Words in time order")
    beats: list[Beat] = Field(default_factory=list, description="Semantic beats")
    language: str = Field(default="auto", description="en | hi | auto")

    @field_validator("words")
    @classmethod
    def _unique_word_ids(cls, words: list[Word]) -> list[Word]:
        ids = [w.id for w in words]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate word ids")
        return words

    @model_validator(mode="after")
    def _beats_reference_known_words(self) -> "Transcript":
        known = {w.id for w in self.words}
        for beat in self.beats:
            for wid in beat.word_ids:
                if wid not in known:
                    raise ValueError(f"beat {beat.id} references unknown word {wid}")
        return self

    @property
    def word_index(self) -> dict[str, Word]:
        """Word ID -> Word lookup."""
        return {w.id: w for w in self.words}
