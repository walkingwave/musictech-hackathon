"""Write a MIDI phrase from a plain-English description.

This is the notes-first counterpart to `interpret`: instead of planning
which audio stems to generate, it returns the actual notes for one part,
which the timeline puts on a MIDI track and plays through the sampler.

Two implementations behind one function, same as `interpret`:

  DeepSeek  writes the phrase. Understands "a walking bass line that
            outlines ii-V-I" or "sparse rhodes stabs on the off-beats".
  rules     a scale-and-arpeggio generator. No network, no key, instant,
            and musically dull — but it always returns something playable
            so the demo survives a dead API key.

Notes are in BEATS from the start of the clip, not seconds: the timeline
already speaks beats for MIDI clips, and that keeps a phrase correct when
the tempo is changed afterwards.
"""

from __future__ import annotations

import json
import logging
import re

import httpx
from pydantic import BaseModel, Field

from . import config

log = logging.getLogger(__name__)

BEATS_PER_BAR = 4

# The sampler transposes one recorded note, so anything below or above this
# range comes back as an obvious artefact rather than a low or high note.
MIN_PITCH = 28
MAX_PITCH = 96

MAX_BARS = 32
MAX_NOTES = 512


class Note(BaseModel):
    pitch: int = Field(description="MIDI note number, 28-96. 60 is middle C.")
    start: float = Field(description="Beats from the start of the phrase.")
    length: float = Field(description="Length in beats. 1.0 is a quarter note.")
    velocity: int = Field(default=90, description="1-127.")


class MidiPhrase(BaseModel):
    """One monophonic-or-chordal part, as notes."""

    name: str = Field(description="Short track name, e.g. 'Rhodes stabs'.")
    instrument: str = Field(
        description="Sound to play it through, as a sampler prompt: one "
        "instrument, one note, e.g. 'electric piano, glassy bell-like tine'."
    )
    bars: int = Field(default=4, description="Length of the phrase in bars.")
    notes: list[Note]
    summary: str = Field(default="", description="One short line for the user.")


SYSTEM = f"""You are a composer writing a short MIDI phrase for a DAW.

You write NOTES, not audio. The result is placed on a MIDI track and played
through a sampler, so it must stand on its own as a musical idea.

Rules:
- `start` and `length` are in BEATS from the start of the phrase, 4 beats to
  the bar. The first beat of bar 1 is start 0.0.
- Stay inside the requested key unless the user asks for something else.
  Chromatic passing tones are fine when the style calls for them.
- Pitch is a MIDI number between {MIN_PITCH} and {MAX_PITCH}; 60 is middle C.
  Put a bass part around 36-52, a mid-register comp around 55-72, and a lead
  or melody around 64-84.
- Write a real phrase, not a scale run: give it rhythm, rests, repetition and
  a shape. A four-bar idea that repeats with a variation beats sixteen bars of
  even eighth notes.
- Chords are allowed — several notes sharing a `start`. Keep them to 3-4 notes.
- Velocity carries the accents. Do not leave every note at the same value.
- `instrument` is a sampler prompt: ONE instrument playing ONE sustained note
  that can be transposed. Never write "chord", "ensemble", "pad" or "section".
- `summary` is one short line for the user. Do not restate the notes.
"""


class Context(BaseModel):
    """Session settings the phrase has to fit."""

    bpm: float | None = None
    key: str | None = None
    mode: str | None = None
    bars: int | None = None
    style: str = ""

    def describe(self) -> str:
        bits = []
        if self.key:
            bits.append(f"key: {self.key} {self.mode or 'major'}")
        if self.bpm:
            bits.append(f"tempo: {round(self.bpm)} BPM")
        if self.style:
            bits.append(f"style: {self.style}")
        if self.bars:
            bits.append(f"length: {self.bars} bars")
        if not bits:
            return ""
        return "The phrase must fit the session it is joining — " + ", ".join(bits) + "."


def compose(text: str, context: Context | None = None) -> tuple[MidiPhrase, str]:
    """Best available phrase for `text`, plus the source that produced it."""
    try:
        return _compose_with_deepseek(text, context), "deepseek"
    except Exception as error:  # noqa: BLE001 - any failure falls back to rules
        log.info("falling back to generated phrase: %s", error)
        return _compose_with_rules(text, context), "rules"


def deepseek_available() -> bool:
    return config.BTG_AGENT_PROVIDER == "deepseek" and bool(config.DEEPSEEK_API_KEY)


def _compose_with_deepseek(text: str, context: Context | None) -> MidiPhrase:
    if not deepseek_available():
        raise RuntimeError("DeepSeek is not configured")

    system = SYSTEM
    if context and (described := context.describe()):
        system = f"{system}\n\n{described}"
    system = f"""{system}

Return ONLY valid json. No Markdown, prose, code fences or comments.
The json object must match this exact shape:
{{
  "name": "short track name",
  "instrument": "sampler prompt: one instrument, one sustained note",
  "bars": 4,
  "notes": [
    {{"pitch": 60, "start": 0.0, "length": 1.0, "velocity": 90}}
  ],
  "summary": ""
}}
"""

    response = httpx.post(
        config.DEEPSEEK_API_URL,
        headers={
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.BTG_AGENT_MODEL,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            # A phrase is a long array of small objects; the plan-sized budget
            # truncates it mid-note and the json fails to parse.
            "max_tokens": 6000,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
        },
        timeout=90.0,
    )
    response.raise_for_status()

    content = response.json()["choices"][0]["message"]["content"]
    if not content:
        raise RuntimeError("DeepSeek returned empty content")

    phrase = MidiPhrase.model_validate(json.loads(content))
    return _sanitize(phrase, context)


def _sanitize(phrase: MidiPhrase, context: Context | None) -> MidiPhrase:
    """Clamp everything the timeline and sampler cannot survive.

    The schema fixes shape, not range: a model can still return pitch 200,
    a negative start, or four hundred bars of notes, and each of those is a
    silent-or-broken track rather than a visible error.
    """
    phrase.bars = max(1, min(MAX_BARS, phrase.bars or 4))
    span = phrase.bars * BEATS_PER_BAR

    kept: list[Note] = []
    for note in phrase.notes:
        if not MIN_PITCH <= note.pitch <= MAX_PITCH:
            continue
        start = max(0.0, round(float(note.start), 4))
        if start >= span:
            continue
        length = max(0.0625, round(float(note.length), 4))
        kept.append(
            Note(
                pitch=int(note.pitch),
                start=start,
                # Trailing notes are trimmed rather than dropped: a phrase that
                # ends on a held chord should still sound, just not overrun.
                length=min(length, span - start),
                velocity=max(1, min(127, int(note.velocity))),
            )
        )
        if len(kept) >= MAX_NOTES:
            break

    kept.sort(key=lambda n: (n.start, n.pitch))
    phrase.notes = kept
    if not phrase.name.strip():
        phrase.name = "MIDI phrase"
    if not phrase.instrument.strip():
        phrase.instrument = "electric piano, glassy bell-like tine"
    return phrase


# --- rules fallback -----------------------------------------------------

_SEMITONE = {
    "c": 0, "c#": 1, "db": 1, "d": 2, "d#": 3, "eb": 3, "e": 4, "f": 5,
    "f#": 6, "gb": 6, "g": 7, "g#": 8, "ab": 8, "a": 9, "a#": 10, "bb": 10, "b": 11,
}
_MAJOR = [0, 2, 4, 5, 7, 9, 11]
_MINOR = [0, 2, 3, 5, 7, 8, 10]

# Register hints, so "write a bass line" does not come back an octave above
# the piano. First match wins, so the more specific words come first.
_REGISTERS = [
    (r"\bsub\s?bass|\bbass\b|\b808\b|contrabass|tuba", 40),
    (r"\blead\b|\bmelody\b|\bsolo\b|\briff\b|\bhook\b|flute|violin|whistle", 72),
    (r"\bpad\b|\bstrings?\b|\bchoir\b|\bharmony\b|\bhorns?\b", 60),
]

# Rhythms in beats, chosen so the fallback is at least idiomatic for the
# register it lands in rather than a metronome.
_PATTERNS = {
    40: [(0.0, 1.0), (1.5, 0.5), (2.0, 1.0), (3.5, 0.5)],
    60: [(0.0, 2.0), (2.0, 2.0)],
    72: [(0.0, 0.5), (0.5, 0.5), (1.0, 1.0), (2.5, 0.5), (3.0, 1.0)],
}


def _compose_with_rules(text: str, context: Context | None) -> MidiPhrase:
    """A plain but playable phrase: scale tones on a register-appropriate grid.

    Deterministic — the same request twice gives the same notes, so a user
    comparing two takes is comparing the prompt, not the dice.
    """
    lowered = text.lower()
    bars = (context.bars if context and context.bars else 4) or 4
    bars = max(1, min(8, bars))

    root = 0
    if context and context.key:
        root = _SEMITONE.get(context.key.strip().lower(), 0)
    scale = _MINOR if (context and context.mode == "minor") else _MAJOR

    base = 60
    for pattern, register in _REGISTERS:
        if re.search(pattern, lowered):
            base = register
            break
    tonic = base + ((root - base) % 12)
    pattern = _PATTERNS[base if base in _PATTERNS else 60]

    # Degrees per bar: a rise and fall rather than a straight run up.
    shape = [0, 2, 4, 2, 5, 4, 2, 0]
    notes: list[Note] = []
    for bar in range(bars):
        for i, (offset, length) in enumerate(pattern):
            degree = shape[(bar * len(pattern) + i) % len(shape)]
            octave, step = divmod(degree, len(scale))
            pitch = tonic + scale[step] + 12 * octave
            notes.append(
                Note(
                    pitch=max(MIN_PITCH, min(MAX_PITCH, pitch)),
                    start=bar * BEATS_PER_BAR + offset,
                    length=length,
                    velocity=100 if offset == 0.0 else 80,
                )
            )

    phrase = MidiPhrase(
        name=text.strip()[:28] or "MIDI phrase",
        instrument=text.strip() or "electric piano, glassy bell-like tine",
        bars=bars,
        notes=notes,
        summary="Written offline — connect the agent for a phrase that follows the description.",
    )
    return _sanitize(phrase, context)
