"""Turn a plain-English request into a concrete generation plan.

Two implementations behind one function:

  Claude   real language understanding. Handles phrasing the keyword
           matcher cannot ("something moody for a rainy night", "swap the
           piano for a Rhodes", "same but slower"), maps instruments we
           do not have onto ones we do, and says so.
  rules    keyword matching. No network, no key, instant. Used when
           Claude is unavailable, so the demo still works offline.

The plan is deliberately explicit about *rhythm*: the groove decides
where notes land in the guide track, and the guide is what fixes the
output's feel. A genre that does not make it into `groove` cannot be
heard in the result, however well the style text describes it.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from pydantic import BaseModel, Field

from . import grooves
from .models import PARTS

log = logging.getLogger(__name__)

MODEL = os.environ.get("BTG_INTERPRET_MODEL", "claude-opus-5")

GROOVE_NAMES = [g.name for g in grooves.ALL]


class TrackSpec(BaseModel):
    part: str = Field(
        description=f"The arranger to use — one of: {', '.join(PARTS)}. This "
        "chooses the rhythmic role, not the sound."
    )
    name: str = Field(
        description="Short track name, from what the user asked for, "
        "e.g. 'xylophone', 'wah bass'."
    )
    instrument: str = Field(
        description="Full description of the sound to generate, e.g. "
        "'bright wooden xylophone, hard mallets' or 'wah-wah electric bass, "
        "envelope filter, funky'. This REPLACES the default instrument "
        "description, so describe the instrument completely."
    )
    style: str = Field(
        default="",
        description="Playing style for this track only, if different from the "
        "arrangement's. Usually empty.",
    )


class Plan(BaseModel):
    """What to generate. Every field is optional except `tracks`."""

    tracks: list[TrackSpec]
    style: str = Field(description="Overall genre or mood, e.g. 'bossa nova'")
    groove: str = Field(description=f"One of: {', '.join(GROOVE_NAMES)}")
    bpm: float | None = None
    key: str | None = Field(default=None, description="Tonic note, e.g. 'A', 'Bb'")
    mode: str | None = Field(default=None, description="'major' or 'minor'")
    bars: int | None = None
    notes: str = Field(
        default="",
        description="One short line for the user: what was understood, and "
        "anything asked for that could not be honoured.",
    )


SYSTEM = f"""You turn a musician's plain-English request into a plan for a
backing-track generator.

Available parts: {', '.join(PARTS)}.
Available grooves: {', '.join(f'{g.name} ({g.description})' for g in grooves.ALL)}.

Rules:
- Pick the groove that matches the requested genre. This matters more than
  it looks: the groove decides where notes fall, and that is what makes a
  genre audible. If no genre is implied, use "straight".
- `part` is only the RHYTHMIC ROLE, not the sound. Any instrument can be
  generated — pick whichever part plays the role you want it to play, then
  describe the actual instrument in `instrument`:
    bass    single low line
    piano   chords, or a pitched melodic/mallet instrument (xylophone,
            marimba, vibraphone, glockenspiel, harp, organ, Rhodes)
    guitar  chords in a mid register, plucked or strummed
    drums   unpitched percussion of any kind (kit, cymbals, timpani,
            congas, tambourine, shaker)
    harmony sustained pads, strings, choir, brass swells
- Never drop an instrument the user asked for. Produce one track for every
  one of them, and if the user says "4 more tracks", return four.
- The user may want several tracks sharing a part — a xylophone and a piano
  are both `part: "piano"`. That is fine and expected. Give each a distinct
  `name`.
- Infer tempo and key only when the user implies them ("slow", "in D minor",
  "90 BPM"). Otherwise leave them null; the app has its own defaults.
- Put genre and mood in the top-level `style`. Use a track's own `style`
  only for something specific to that instrument.
- `notes` is one short line addressed to the user. Do not restate the plan
  back to them — mention only substitutions, ambiguity, or anything ignored.
"""


class Context(BaseModel):
    """What is already in the session, so additions match it."""

    style: str = ""
    bpm: float | None = None
    key: str | None = None
    mode: str | None = None
    bars: int | None = None
    existing_parts: list[str] = Field(default_factory=list)

    def describe(self) -> str:
        if not (self.style or self.existing_parts):
            return ""
        lines = ["The session already contains:"]
        if self.existing_parts:
            lines.append(f"- parts: {', '.join(self.existing_parts)}")
        if self.style:
            lines.append(f"- style: {self.style}")
        if self.bpm:
            lines.append(f"- tempo: {round(self.bpm)} BPM")
        if self.key:
            lines.append(f"- key: {self.key} {self.mode or ''}".strip())
        lines.append(
            "Unless the user is explicitly changing them, keep style, tempo and key "
            "as they are and return only the NEW parts to add. Everything must sit "
            "in the same arrangement as what is already there."
        )
        return "\n".join(lines)


def interpret(text: str, context: Context | None = None) -> Plan:
    """Best available interpretation of `text`, never raising.

    `context` describes the existing session so that a follow-up request
    adds to the arrangement rather than starting a conflicting one.
    """
    try:
        return _interpret_with_claude(text, context)
    except Exception as error:  # noqa: BLE001 - any failure falls back to rules
        log.info("falling back to keyword parsing: %s", error)
        return _interpret_with_rules(text, context)


def claude_available() -> bool:
    """Whether the Claude path can run.

    Note the SDK resolves credentials from an `ant auth login` profile as
    well as the environment, so a missing ANTHROPIC_API_KEY does not by
    itself mean there is no key — check the profile directory too.

    Constructing a client is *not* a valid check: `anthropic.Anthropic()`
    succeeds with no credentials at all and only raises at request time.
    """
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False

    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True

    config_dir = Path(os.environ.get("ANTHROPIC_CONFIG_DIR", Path.home() / ".config" / "anthropic"))
    return (config_dir / "credentials").is_dir()


def _interpret_with_claude(text: str, context: Context | None) -> Plan:
    import anthropic

    system = SYSTEM
    if context and (described := context.describe()):
        system = f"{SYSTEM}\n\n{described}"

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": text}],
        output_format=Plan,
    )

    if response.stop_reason == "refusal":
        raise RuntimeError("request declined")

    plan = response.parsed_output
    return _sanitize(plan)


def _sanitize(plan: Plan) -> Plan:
    """Drop anything the rest of the pipeline cannot act on.

    The schema constrains shape, not vocabulary — a model can still return
    a part or groove we do not implement, and that would fail much later
    with a confusing error.
    """
    plan.tracks = [t for t in plan.tracks if t.part in PARTS]
    if plan.groove not in GROOVE_NAMES:
        plan.groove = grooves.for_style(plan.style).name
    if plan.mode not in ("major", "minor", None):
        plan.mode = None
    if plan.bpm is not None and not 20 <= plan.bpm <= 300:
        plan.bpm = None
    if plan.bars is not None and not 1 <= plan.bars <= 128:
        plan.bars = None
    return plan


# --- keyword fallback ---------------------------------------------------

# Instrument -> (part, description). `part` is the rhythmic role the
# arranger plays; the description becomes the prompt. Anything not listed
# here is still handled by the Claude path; this table only has to cover
# enough that the offline demo is not obviously broken.
INSTRUMENTS: dict[str, tuple[str, str]] = {
    # bass role
    "bass": ("bass", ""),
    "bassline": ("bass", ""),
    "wah bass": ("bass", "wah-wah electric bass, envelope filter, funky"),
    "waw bass": ("bass", "wah-wah electric bass, envelope filter, funky"),
    "sub": ("bass", "deep sub bass, sine-like, minimal"),
    "808": ("bass", "808 sub bass, long decay"),
    "upright bass": ("bass", "upright double bass, woody, fingered"),
    "synth bass": ("bass", "analog synth bass, saw wave, punchy"),
    # pitched / mallet -> piano role
    "piano": ("piano", ""),
    "keys": ("piano", ""),
    "rhodes": ("piano", "Rhodes electric piano, warm tines, light chorus"),
    "wurli": ("piano", "Wurlitzer electric piano, gritty, vibrato"),
    "organ": ("piano", "drawbar organ, rotary speaker"),
    "xylophone": ("piano", "bright wooden xylophone, hard mallets"),
    "marimba": ("piano", "warm marimba, soft mallets, woody"),
    "vibraphone": ("piano", "vibraphone, motor vibrato, soft mallets"),
    "vibes": ("piano", "vibraphone, motor vibrato, soft mallets"),
    "glockenspiel": ("piano", "glockenspiel, bright metallic bells"),
    "celesta": ("piano", "celesta, delicate bell-like tone"),
    "harp": ("piano", "concert harp, plucked, resonant"),
    # guitar role
    "guitar": ("guitar", ""),
    "acoustic guitar": ("guitar", "steel-string acoustic guitar, strummed"),
    "electric guitar": ("guitar", "clean electric guitar, warm amp"),
    "nylon guitar": ("guitar", "nylon-string classical guitar, fingerpicked"),
    "banjo": ("guitar", "five-string banjo, bright plucked"),
    "ukulele": ("guitar", "ukulele, bright nylon strum"),
    # percussion -> drums role
    "drums": ("drums", ""),
    "drum kit": ("drums", ""),
    "percussion": ("drums", "hand percussion, shakers and woodblocks, dry"),
    "cymbals": ("drums", "crash and ride cymbals, shimmering, no drums"),
    "timpani": ("drums", "orchestral timpani, deep resonant mallet hits"),
    "congas": ("drums", "congas and bongos, warm hand drums"),
    "tambourine": ("drums", "tambourine, bright jingles"),
    "shaker": ("drums", "shaker, steady dry rattle"),
    # sustained -> harmony role
    "harmony": ("harmony", ""),
    "choir": ("harmony", "warm choir pad, sustained aahs"),
    "strings": ("harmony", "string ensemble, legato sustained"),
    "pad": ("harmony", "soft synth pad, slow attack"),
    "brass": ("harmony", "brass section swells, warm"),
}

EVERYTHING = ("full band", "whole band", "everything", "full arrangement")
DEFAULT_BAND = ("bass", "drums", "piano", "harmony")


def _fuzzy_instruments(lower: str, claimed: list[tuple[int, int]]) -> list[tuple[str, str, str]]:
    """Catch misspelled instrument names the exact matcher missed.

    People type "cylophone". The Claude path reads through that without
    help; the keyword path would silently drop the track, which is the
    failure mode worth avoiding — a dropped instrument looks like the app
    ignoring you.
    """
    import difflib

    out: list[tuple[str, str, str]] = []
    for match in re.finditer(r"[a-z]{5,}", lower):
        if any(a <= match.start() < b for a, b in claimed):
            continue
        close = difflib.get_close_matches(match.group(), INSTRUMENTS, n=1, cutoff=0.8)
        if close:
            part, instrument = INSTRUMENTS[close[0]]
            out.append((part, instrument, close[0]))
    return out


def _interpret_with_rules(text: str, context: Context | None = None) -> Plan:
    lower = f" {text.lower()} "

    if any(phrase in lower for phrase in EVERYTHING):
        found = [(p, "", p) for p in DEFAULT_BAND]
    else:
        # Longest names first, so "wah bass" is not consumed by "bass" and
        # "acoustic guitar" is not consumed by "guitar".
        found, claimed = [], []
        for word in sorted(INSTRUMENTS, key=len, reverse=True):
            index = lower.find(f" {word}")
            if index < 0 or any(a <= index < b for a, b in claimed):
                continue
            claimed.append((index, index + len(word) + 1))
            part, instrument = INSTRUMENTS[word]
            found.append((part, instrument, word))
        found.sort(key=lambda f: lower.find(f[2]))
        found.extend(_fuzzy_instruments(lower, claimed))

    groove = grooves.for_style(lower)
    style = groove.name if groove.name != "straight" else ""

    # A request with no genre of its own inherits the session's, so an
    # "add a piano" cannot silently reset the groove to straight.
    if groove.name == "straight" and context and context.style:
        style = context.style
        groove = grooves.for_style(style)

    return Plan(
        tracks=[
            TrackSpec(part=part, name=word.replace(" ", "-"), instrument=instrument)
            for part, instrument, word in found
        ],
        style=style,
        groove=groove.name,
        notes="" if found else "No instruments recognised — try naming them, e.g. 'bass and drums'.",
    )
