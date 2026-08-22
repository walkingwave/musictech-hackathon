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
from pathlib import Path

from pydantic import BaseModel, Field

from . import grooves
from .models import PARTS

log = logging.getLogger(__name__)

MODEL = os.environ.get("BTG_INTERPRET_MODEL", "claude-opus-5")

GROOVE_NAMES = [g.name for g in grooves.ALL]


class TrackSpec(BaseModel):
    part: str = Field(description=f"One of: {', '.join(PARTS)}")
    style: str = Field(
        description="Sound and playing style for this part specifically, "
        "e.g. 'muted fingerstyle, warm tone'. May be empty."
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
- If the user asks for an instrument that is not in the list, map it to the
  closest available part and say so in `notes` — e.g. organ or Rhodes to
  piano, strings or choir to harmony, synth bass to bass.
- Infer tempo and key only when the user implies them ("slow", "in D minor",
  "90 BPM"). Otherwise leave them null; the app has its own defaults.
- Put genre and mood in the top-level `style`. Use a track's own `style`
  only for something specific to that instrument.
- `notes` is one short line addressed to the user. Do not restate the plan
  back to them — mention only substitutions, ambiguity, or anything ignored.
"""


def interpret(text: str) -> Plan:
    """Best available interpretation of `text`, never raising."""
    try:
        return _interpret_with_claude(text)
    except Exception as error:  # noqa: BLE001 - any failure falls back to rules
        log.info("falling back to keyword parsing: %s", error)
        return _interpret_with_rules(text)


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


def _interpret_with_claude(text: str) -> Plan:
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM,
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

PART_WORDS = {
    "bass": ("bassline", "bass line", "bass", "sub", "808"),
    "drums": ("drums", "drum", "percussion", "beat", "kick", "groove", "kit"),
    "piano": ("piano", "keys", "keyboard", "rhodes", "wurli", "organ", "chords"),
    "guitar": ("guitar", "gtr", "strum", "riff"),
    "harmony": ("harmony", "harmonies", "choir", "pad", "strings", "backing vocal"),
}
EVERYTHING = ("full band", "whole band", "everything", "full arrangement")


def _interpret_with_rules(text: str) -> Plan:
    lower = f" {text.lower()} "

    if any(phrase in lower for phrase in EVERYTHING):
        parts = list(PARTS)
    else:
        parts = [p for p in PARTS if any(w in lower for w in PART_WORDS[p])]

    groove = grooves.for_style(lower)
    style = groove.name if groove.name != "straight" else ""

    return Plan(
        tracks=[TrackSpec(part=p, style="") for p in parts],
        style=style,
        groove=groove.name,
        notes="" if parts else "No instruments recognised — try naming them, e.g. 'bass and drums'.",
    )
