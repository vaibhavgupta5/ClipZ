"""Spoken keywords -> relevant visuals ("visual cues"), deterministically.

The transcript is UNTRUSTED DATA (rule 6): words are only tokenised and matched against
library tags/aliases; nothing in them is ever followed. Timing comes from our own word
IDs (rule 2), so a cue lands exactly when its word is said.

Pipeline: surviving words -> terms (unigrams + bigrams, stopwords dropped, ambiguous brand
words only when capitalised) -> best library match per term (icons by exact alias, videos,
images, reaction GIFs by IDF tag score) -> greedy plan honouring spacing, density and
"never the same asset twice".
"""

import re
from dataclasses import dataclass

from app.providers.local import AssetCatalog
from app.timeline import snap
from app.timeline.errors import OpError
from app.timeline.schema import Timeline

AUTO_KINDS = ("icon", "video", "image", "gif")  # memes need jokes + text: the model picks those
KIND_WEIGHT = {"icon": 1.0, "video": 1.1, "image": 1.0, "gif": 0.9}
MIN_SCORE = 3.0  # tag on <~25% of a library; below this a match reads as random

STOPWORDS = frozenset(
    """
a an the and or but if then so than that this these those there here it its it's i me my we
our you your he she they them their his her is am are was were be been being do does did
doing have has had having will would can could should may might must shall to of in on at by
for with from up down out over under about into through after before again just also very
really too not no yes all any some more most other such only own same few each both what
which who whom whose when where why how because while as until like get got go going gonna
wanna know think say said says make made thing things stuff way lot lots kind sort mean
actually basically literally right okay ok yeah guys people one two let lets let's dont don't
im i'm youre you're thats that's isnt isn't wasnt can't cant wont won't didn't didnt now even
still much many well good bad new time today something someone everyone anything nothing
hai hain ki ka ke ko se me mein main mai toh to bhi aur ye yeh wo woh kya nahi nahin na ho
tha thi the raha rahe rahi kar karo karna karte karta kiya bhai yaar ek jo jab tab ab sab
kuch bahut apna apne hum tum aap mera meri tera teri uska uski unka liye wala wali wale haan
matlab hota hoti hote gaya gayi diya liya sakte sakta chahiye phir fir agar lekin par pe
world build building builds shape learn learning learnings era state fact problem part point
level question answer help helping helps able start started need needs talk talking share
discuss video videos channel subscribe also big small first last next better best fast faster
""".split()
)

TECH_ALWAYS = frozenset({
    "reactjs", "python", "javascript", "typescript", "docker", "kubernetes",
    "fastapi", "django", "nextjs", "nodejs", "tailwind", "postgres", "postgresql",
    "redis", "mongodb", "cursor", "claude", "gemini", "openai", "copilot", "chatgpt",
    "deepseek", "mistral", "qwen", "grok", "pytorch", "tensorflow", "huggingface",
    "linux", "ubuntu", "neovim", "figma", "langchain", "ollama", "supabase", "vercel",
    "firebase", "digitalocean", "cloudflare", "solana", "bitcoin", "ethereum",
})

# Everyday words that double as brand names: a brand only when the transcript capitalises
# them (Whisper writes "React", "Rust", "Apple", "Go" for the products) or when in a compound.
AMBIGUOUS = frozenset({
    "react", "rust", "swift", "notion", "apple", "meta", "snowflake", "go",
    "flutter", "spotify", "discord", "opus", "sonnet", "x", "torch",
    "container", "containers", "email", "node", "ts", "js", "eth", "mac", "reels", "tweet",
})  # fmt: skip


@dataclass(frozen=True)
class Term:
    """A spoken word or two-word phrase with its output timing."""

    first: str  # first word ID
    last: str  # last word ID
    text: str
    start_s: float
    end_s: float


@dataclass(frozen=True)
class Cue:
    """A visual matched to what is being said."""

    word: str  # anchor: last word of the phrase
    first: str
    phrase: str
    kind: str
    asset_id: str
    title: str
    score: float
    start_s: float
    end_s: float


def _clean(text: str) -> str:
    return re.sub(r"[^\w+]", "", text.lower())


def spoken_terms(tl: Timeline) -> list[Term]:
    """Unigrams and bigrams of the words that survive the cuts, stopwords removed."""
    words = []
    for w in tl.transcript.words:
        try:
            start = snap.output_time_of(tl, w.id, "start")
            end = snap.output_time_of(tl, w.id, "end")
        except OpError:
            continue  # cut away
        words.append((w, _clean(w.text), start, end))
    terms: list[Term] = []
    for i, (w, token, start, end) in enumerate(words):
        if _keep(token, w.display_text or w.text):
            terms.append(Term(w.id, w.id, token, start, end))
        if i + 1 < len(words):
            w2, token2, _, end2 = words[i + 1]
            if token and token2 and token not in STOPWORDS and token2 not in STOPWORDS:
                terms.append(Term(w.id, w2.id, f"{token} {token2}", start, end2))
    return terms


def _keep(token: str, display: str) -> bool:
    if not token or token in STOPWORDS or token.isdigit():
        return False
    if token in TECH_ALWAYS:
        return True
    if token in AMBIGUOUS:
        return display[:1].isupper() and not display.isupper()
    return len(token) >= 3


def find_cues(
    tl: Timeline,
    catalog: AssetCatalog,
    kinds: tuple[str, ...] = AUTO_KINDS,
    alternatives: int = 3,
) -> list[Cue]:
    """Relevant visuals per spoken term, best first per term (up to ``alternatives`` so a
    repeated word can fall back to an unused asset). A phrase suppresses the single words
    inside it when it scores at least as well. Sorted by time, then score."""
    per_span: dict[tuple[str, str], list[Cue]] = {}
    for term in spoken_terms(tl):
        found: list[Cue] = []
        for kind in kinds:
            for score, item in catalog.match(kind, term.text)[:alternatives]:
                weighted = round(score * KIND_WEIGHT.get(kind, 1.0), 3)
                if weighted >= MIN_SCORE:
                    found.append(
                        Cue(
                            term.last,
                            term.first,
                            term.text,
                            kind,
                            item.id,
                            item.title,
                            weighted,
                            term.start_s,
                            term.end_s,
                        )
                    )
        if found:
            found.sort(key=lambda c: -c.score)
            per_span[(term.first, term.last)] = found[:alternatives]
    best = {span: cues[0].score for span, cues in per_span.items()}
    phrases = [span for span in per_span if span[0] != span[1]]
    kept = [
        cue
        for span, cues in per_span.items()
        if span[0] != span[1] or not any(best[p] >= best[span] and span[0] in p for p in phrases)
        for cue in cues
    ]
    return sorted(kept, key=lambda c: (c.start_s, -c.score))


def plan_cues(
    cues: list[Cue],
    duration_s: float,
    *,
    gap_s: float,
    per_s: float,
    hook_s: float,
    used: set[str] | None = None,
    busy: list[tuple[float, float]] | None = None,
) -> list[Cue]:
    """Greedy best-first pick: >= gap_s between starts, at most one per ``per_s`` seconds,
    nothing before ``hook_s``, never inside ``busy`` windows, never an asset twice."""
    taken = set(used or ())
    budget = max(1, int(duration_s / per_s))
    chosen: list[Cue] = []
    for cue in sorted(cues, key=lambda c: -c.score):
        if len(chosen) >= budget:
            break
        if cue.start_s < hook_s or cue.asset_id in taken:
            continue
        if any(abs(cue.start_s - c.start_s) < gap_s for c in chosen):
            continue
        if any(s - 0.05 <= cue.start_s <= e + 0.05 for s, e in busy or ()):
            continue
        chosen.append(cue)
        taken.add(cue.asset_id)
    return sorted(chosen, key=lambda c: c.start_s)


def describe(cues: list[Cue], limit: int = 30) -> str:
    """Compact lines for the Director context: 'w12 "chatgpt" -> icon icons:openai (...)'."""
    firsts: dict[tuple[str, str], Cue] = {}
    for c in cues:
        if (c.first, c.word) not in firsts or c.score > firsts[(c.first, c.word)].score:
            firsts[(c.first, c.word)] = c
    top = sorted(firsts.values(), key=lambda c: -c.score)[:limit]
    return "\n".join(
        f'{c.word} "{c.phrase}" {c.start_s:.1f}s -> {c.kind} {c.asset_id} ({c.title[:40]})'
        for c in sorted(top, key=lambda c: c.start_s)
    )
