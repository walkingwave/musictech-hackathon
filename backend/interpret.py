"""Turn a plain-English request into a concrete generation plan.

Two implementations behind one function:

  DeepSeek real language understanding. Handles phrasing the keyword
           matcher cannot ("something moody for a rainy night", "swap the
           piano for a Rhodes", "same but slower"), maps instruments we
           do not have onto ones we do, and says so.
  rules    keyword matching. No network, no key, instant. Used when
           DeepSeek is unavailable, so the demo still works offline.

The plan is deliberately explicit about *rhythm*: the groove decides
where notes land in the guide track, and the guide is what fixes the
output's feel. A genre that does not make it into `groove` cannot be
heard in the result, however well the style text describes it.
"""

from __future__ import annotations

import logging
import json
import os
import re

import httpx
from pydantic import BaseModel, Field

from . import config, grooves
from .models import PARTS

log = logging.getLogger(__name__)

GROOVE_NAMES = [g.name for g in grooves.ALL]


def hum_target(text: str) -> str:
    """Resolve the musical role requested for a recorded hum.

    The UI offers an explicit control, but this keeps API clients and prompt-led
    flows useful: “bassline”, “low end”, and “bass” choose bass; every other
    request defaults to preserving the hum as a melody.
    """
    lowered = text.lower()
    return "bass" if re.search(r"\bbass(?:line)?\b|\blow end\b|\bsub\b", lowered) else "melody"


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
    midi: bool = Field(
        default=False,
        description="True to WRITE this part as editable MIDI notes instead of "
        "generating audio. Choose it when the user asks for notes, MIDI, a "
        "written phrase, or something they want to edit afterwards.",
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
    production: str = Field(
        default="",
        description="How the whole arrangement is recorded — room, mics, era, "
        "tape, mix character. Shared by every part, which is what makes "
        "separately generated stems sound like one band.",
    )
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
    harmony sustained pads, a string or brass SECTION, choir — held chords
            behind everything else, never the tune
    melody  the tune itself: a single lead line. A solo violin, flute, sax,
            trumpet, cello or lead synth playing a melody is `melody`, not
            `harmony`. If the user names one instrument and expects to hear
            it out front, it is `melody`.
    mix     the WHOLE band rendered as ONE track instead of separate parts.
            This is opt-in, never the default: use it only when the user
            explicitly asks for a single track — "as one track", "one file",
            "don't split it", "a full mix", "one whole song in one go". A
            plain "make me an EDM track" is NOT that; it wants a playable
            arrangement, which means separate parts they can mix and edit.
            When you do use it, return it as the ONLY track and describe the
            whole band in its `instrument`.
    free    an instrument with no clear rhythmic role
- NEVER substitute one named instrument for another. If the user says
  "piano chords", the part is `piano` and the instrument says piano — not
  guitar, not Rhodes, not "keys". A named instrument is the one thing in the
  request you are not allowed to reinterpret. Only when the user names no
  instrument at all ("something warm underneath") do you get to choose one.
  When two parts could carry the role, the instrument's own family decides:
  anything struck or keyed is `piano`, anything strummed or plucked with
  frets is `guitar`.
- `instrument` must repeat the instrument the user named, in its own words,
  as the FIRST thing it says. "acoustic grand piano, close-miked, warm" is
  right for "piano chords"; "warm chordal instrument" is not.
- Never drop an instrument the user asked for. Produce one track for every
  one of them, and if the user says "4 more tracks", return four.
- The user may want several tracks sharing a part — a xylophone and a piano
  are both `part: "piano"`. That is fine and expected. Give each a distinct
  `name`.
- Tempo, key and mode are part of the brief even when the user gives no
  numbers. A mood implies them: "slow and sad" is not 120 BPM in C major.
  Set `bpm`, `key` and `mode` whenever the request carries ANY musical
  intent — a genre, a mood, an energy, a reference artist — and pick values
  that a musician would actually choose for it:
    sad, mournful, heartbroken     60-75 BPM, minor
    calm, dreamy, ambient          70-90 BPM, major or minor
    warm, hopeful, folky           90-110 BPM, major
    driving, upbeat, pop/rock      110-130 BPM, major
    dance, house, energetic        120-128 BPM, minor
    aggressive, dark, trap/metal   70-90 or 140-160 BPM, minor
  Leave them null ONLY when the request is purely about the sound of one
  instrument and says nothing about the music ("swap the bass for a Rhodes").
  Changing them is expected and wanted: the app updates its tempo and key
  boxes from your answer, so a sad ballad request should return a slow tempo
  and a minor key even if the session currently says 120 BPM C major.
- `production` describes the RECORDING, not the music, and every part of the
  arrangement is generated with it. This is the only thing making separately
  generated stems sound like one band in one room, so it has to be concrete:
  room size, mic distance, era, tape or digital, mix character. Six to twelve
  words. E.g. "close-miked in a small dry studio, warm analog tape, gentle
  compression" or "roomy 70s live take, ribbon mics, soft saturation". Never
  name an instrument in it.
- Put genre and mood in the top-level `style`. Use a track's own `style`
  only for something specific to that instrument.
- `notes` is one short line addressed to the user. Do not restate the plan
  back to them — mention only substitutions, ambiguity, or anything ignored.
- Set `midi: true` on a track when the user wants NOTES rather than a
  recording: "write me a bassline", "compose a piano phrase", "give me a
  MIDI riff I can edit". A MIDI track arrives in the piano roll and can be
  edited note by note. Everything else stays `midi: false` and is generated
  as audio.
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
            "Return only the NEW parts to add, and keep style, tempo and key as "
            "they are unless the request carries a mood or genre of its own — a "
            "request for something slow and sad should change them, a request to "
            "swap one instrument should not. Everything must sit in the same "
            "arrangement as what is already there."
        )
        return "\n".join(lines)

# What the user asked the request to produce. Passed explicitly by the UI so
# the agent never has to infer it from phrasing — "give me a drum backing
# track" is one stem to a musician and a whole arrangement to a model reading
# the word "track", and guessing wrong either way is worse than asking.
MODES = ("stems", "single", "midi")

MODE_RULES = {
    "stems": (
        "MODE: separate tracks. Return one track per instrument, `midi` false "
        "on all of them, and NEVER `part: \"mix\"`. Return exactly the "
        "instruments the user named; choose the parts yourself only if they "
        "named none."
    ),
    "single": (
        "MODE: one track. Return EXACTLY ONE track with `part: \"mix\"` and "
        "`midi` false, describing the whole band in its `instrument`. Never "
        "return more than one track in this mode."
    ),
    "midi": (
        "MODE: MIDI. Return one track per instrument with `midi` true on every "
        "one, and NEVER `part: \"mix\"`. These become editable note tracks, so "
        "describe an instrument that plays notes, not a full mix."
    ),
}


def _apply_mode(plan: Plan, mode: str | None) -> Plan:
    """Force the plan to match the mode the user picked.

    The system prompt already asks for it, but a mode the user chose from a
    control is a fact, not a preference — so it is enforced here rather than
    hoped for. Anything the model returns that contradicts it is corrected.
    """
    if mode not in MODES:
        return plan

    if mode == "single":
        if len(plan.tracks) != 1 or plan.tracks[0].part != "mix":
            # Collapse whatever came back into one mix track, keeping every
            # instrument named so the description still covers the band.
            described = ", ".join(
                t.instrument.strip() or t.name for t in plan.tracks if (t.instrument or t.name)
            )
            plan.tracks = [
                TrackSpec(
                    part="mix",
                    name=plan.style.title() if plan.style else "Full mix",
                    instrument=described or plan.style,
                    style="",
                    midi=False,
                )
            ]
        plan.tracks[0].midi = False
        return plan

    # stems and midi both want per-instrument tracks; a mix is never one.
    if any(t.part == "mix" for t in plan.tracks):
        expanded: list[TrackSpec] = []
        for track in plan.tracks:
            if track.part != "mix":
                expanded.append(track)
                continue
            # One mix asked for as separate parts becomes the band it stood
            # for, all sharing its description.
            for part in ("drums", "bass", "piano", "melody"):
                expanded.append(
                    TrackSpec(
                        part=part,
                        name=part.title(),
                        instrument=track.instrument,
                        style=track.style,
                        midi=False,
                    )
                )
        plan.tracks = expanded

    for track in plan.tracks:
        track.midi = mode == "midi"
    return plan



def interpret(text: str, context: Context | None = None) -> Plan:
    """Best available interpretation of `text`, never raising.

    `context` describes the existing session so that a follow-up request
    adds to the arrangement rather than starting a conflicting one.
    """
    plan, _ = interpret_with_source(text, context)
    return plan


def interpret_with_source(
    text: str, context: Context | None = None, mode: str | None = None
) -> tuple[Plan, str]:
    """Best available interpretation plus the provider that actually produced it.

    `mode` is the output shape the user picked in the UI — separate tracks, one
    track, or MIDI. It is applied to whichever interpreter answered, so the
    offline fallback obeys it too.
    """
    try:
        plan, source = _interpret_with_deepseek(text, context, mode), "deepseek"
    except Exception as error:  # noqa: BLE001 - any failure falls back to rules
        log.info("falling back to keyword parsing: %s", error)
        plan, source = _interpret_with_rules(text, context), "rules"
    return _apply_mode(plan, mode), source


def interpreter_name() -> str:
    """Name the interpreter that will be tried first for UI/debug output."""
    return "deepseek" if deepseek_available() else "rules"


def deepseek_available() -> bool:
    return config.BTG_AGENT_PROVIDER == "deepseek" and bool(config.DEEPSEEK_API_KEY)


def _interpret_with_deepseek(text: str, context: Context | None, mode: str | None = None) -> Plan:
    """Use DeepSeek JSON mode to produce the same Plan shape the UI already executes."""
    if not deepseek_available():
        raise RuntimeError("DeepSeek is not configured")

    system = _deepseek_system_prompt()
    if mode in MODE_RULES:
        # Last, so it wins over anything general the prompt said about shape.
        system = f"{system}\n\n{MODE_RULES[mode]}"
    if context and (described := context.describe()):
        system = f"{system}\n\n{described}"

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
            "max_tokens": 1600,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
        },
        timeout=30.0,
    )
    response.raise_for_status()

    payload = response.json()
    content = payload["choices"][0]["message"]["content"]
    if not content:
        raise RuntimeError("DeepSeek returned empty content")

    plan = Plan.model_validate(json.loads(content))
    return _sanitize(plan)


def _deepseek_system_prompt() -> str:
    return f"""{SYSTEM}

Return ONLY valid json. Do not include Markdown, prose, code fences, or comments.
The json object must match this exact shape:
{{
  "tracks": [
    {{
      "part": "bass | piano | guitar | drums | harmony | melody | mix | free",
      "name": "short track name",
      "instrument": "complete sound description, or empty string for the default",
      "style": "track-specific style, usually empty",
      "midi": false
    }}
  ],
  "style": "overall genre or mood, empty string if none",
  "groove": "{' | '.join(GROOVE_NAMES)}",
  "bpm": null,
  "key": null,
  "mode": null,
  "bars": null,
  "production": "room, mics, era, tape and mix character - no instruments",
  "notes": ""
}}

Important:
- You are not allowed to call Stable Audio 3 directly.
- You only create a plan for this app to execute.
- Choose `free` for unknown instruments, textures, effects, or anything with no clear rhythmic role.
- Keep the existing session tempo, key and style when the request is only
  about adding or changing a sound. Change them when the request carries a
  mood or genre of its own — a sad ballad in a session set to 120 BPM C
  major should come back slow and minor.
- A named ENSEMBLE counts as naming its members. "Jazz quartet" is four
  tracks — the named lead plus the rhythm section (e.g. sax, piano, double
  bass, drums); a trio is three; "full band" is drums, bass, a chordal part
  and a lead. "A sax solo with a jazz quartet" means the quartet plays too:
  return every member, not just the soloist.
- Return EXACTLY the instruments the user named, and nothing else. "Give me a
  drum backing track" is one drums track — not drums plus a bass, a piano and
  a lead to go with it. Never add a part they did not ask for, however
  incomplete the result sounds to you: they are building the arrangement one
  track at a time, and the parts they have not asked for yet are the ones
  they are about to.
- Only when the user names NO instrument at all ("make me something upbeat",
  "a lo-fi beat") do you choose the parts yourself: drums, bass, a chordal
  part and a melody. Collapse that into a single `mix` track only when they
  explicitly ask for one track.
"""


# Instruments whose family decides the part, whatever the model answered.
# The model reassigns "piano chords" to `guitar` often enough that this is
# worth enforcing in code: the part chooses the arranger, so a wrong part
# is not a wording problem, it is the wrong notes in the wrong register.
PART_BY_INSTRUMENT: list[tuple[str, str]] = [
    (r"\bdrum|\bkit\b|percussion|conga|bongo|tabla|timpani|tambourine|shaker|cymbal|hi-?hat|snare", "drums"),
    (r"\bbass\b|\b808\b|contrabass|upright bass|sub ?bass|tuba", "bass"),
    # Role words beat instrument family: a "lead guitar" is a lead that
    # happens to be a guitar, and arranging it as comping is why a bebop
    # request came back with two chordal parts and one horn instead of two
    # soloists trading. "solo" is deliberately absent — "solo piano" means a
    # piano, not a lead line.
    (r"\blead\b|\bmelody\b|\btopline\b|\btheme\b|\bsoloist\b", "melody"),
    # Instruments that only ever play a single line.
    (r"\bviolin|\bfiddle\b|\bcello\b|\bviola\b|\bflute\b|\bsax|\btrumpet\b|\bclarinet\b|\boboe\b|\bwhistle\b", "melody"),
    (r"\bpiano\b|rhodes|wurlitzer|clavinet|harpsichord|celesta|organ|keys\b|xylophone|marimba|vibraphone|glockenspiel|kalimba|mallet", "piano"),
    (r"\bguitar\b|\bukulele\b|banjo|mandolin|sitar|\bharp\b|\blute\b", "guitar"),
    (r"\bpad\b|\bstrings?\b|\bchoir\b|\bensemble\b|\bsection\b|brass|\bhorns?\b|\bswells?\b", "harmony"),
]


def _part_for_instrument(text: str) -> str | None:
    """The part a named instrument belongs to, or None if nothing is named."""
    lowered = text.lower()
    for pattern, part in PART_BY_INSTRUMENT:
        if re.search(pattern, lowered):
            return part
    return None


def _sanitize(plan: Plan) -> Plan:
    """Drop anything the rest of the pipeline cannot act on.

    The schema constrains shape, not vocabulary — a model can still return
    a part or groove we do not implement, and that would fail much later
    with a confusing error.
    """
    plan.tracks = [t for t in plan.tracks if t.part in PARTS]

    # A named instrument wins over the part the model chose for it. Asking
    # for piano chords and being handed a guitar is the single most visible
    # way this feature fails, and it is entirely fixable here.
    for track in plan.tracks:
        # A mix describes the whole band, so of course it names instruments.
        # Correcting it would turn "make me an EDM track" into a drum stem,
        # which is exactly what happened before this check.
        if track.part == "mix":
            continue
        named = _part_for_instrument(f"{track.name} {track.instrument}")
        if named and named != track.part and named in PARTS:
            log.info(
                "part corrected: %r was %s, instrument says %s",
                track.name, track.part, named,
            )
            track.part = named

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
# here is still handled by the DeepSeek path; this table only has to cover
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


# Words that hint at a rhythmic role when the instrument is unknown to us.
# Only used to pick which guide to build - the prompt text is always the
# user's own words.
ROLE_HINTS = (
    ("bass", ("bass", "sub", "808", "contrabass", "tuba")),
    ("drums", ("drum", "cymbal", "perc", "timpani", "conga", "bongo", "snare",
               "kick", "hat", "tabla", "djembe", "shaker", "tambourine", "gong")),
    ("harmony", ("pad", "string", "choir", "vocal", "aah", "brass", "horn",
                 "swell", "drone", "ambient")),
    ("guitar", ("guitar", "banjo", "mandolin", "ukulele", "sitar", "lute")),
)

# Leading words that are instruction, not instrument. Stripped repeatedly,
# because a real request stacks several: "add 4 more tracks, ...".
FILLER = re.compile(
    r"^(?:add|also|give me|i want|i need|can you|please|make|create|generate|"
    r"another|some|a|an|the|more|of|with|tracks?|parts?|\d+|one|two|three|"
    r"four|five|six|seven|eight|nine|ten|couple|few)\b\s*"
)


# Passing an unrecognised phrase through as an instrument is right when the
# user is clearly asking for one, and wrong otherwise: "something moody and
# slower" is a description of the arrangement, and turning it into two
# tracks called "something moody" and "slower" is worse than doing nothing.
# Without a verb like this, the fallback only accepts instruments it knows.
ADD_INTENT = re.compile(
    r"\b(?:add|another|more|extra|include|layer|throw in|put in|give me|"
    r"i want|i need|with a|with an)\b"
)


def _role_for(phrase: str) -> str:
    """Which guide an unknown instrument should follow.

    Falls back to `free` — a plain chord bed — rather than guessing a
    specific role. Routing an unrecognised instrument to `piano` does not
    just fail to help, it actively imposes block-chord comping on something
    that may play nothing like a piano. A neutral bed gives the output the
    key and the bar grid and leaves everything else to the prompt.
    """
    for part, hints in ROLE_HINTS:
        if any(h in phrase for h in hints):
            return part
    return "free"


def _split_requests(text: str) -> list[str]:
    """Break a request into one fragment per instrument.

    Splitting on the list separators the user typed is far more robust than
    matching a vocabulary: it works for instruments we have never heard of,
    which is the whole point.
    """
    text = re.sub(r"\b(?:in|with)\s+(?:a|an|the)?\s*[\w\s-]*\bstyle\b", " ", text)
    parts = re.split(r"[,:;]|\band\b|\bplus\b|/|&|\n", text.lower())

    # A fragment can still name several instruments with no separator at all
    # ("piano drums guitar bossa nova"). Split those on the instrument names
    # themselves, or the whole run becomes one track.
    out: list[str] = []
    for fragment in parts:
        fragment = fragment.strip(" .!?")
        if fragment:
            out.extend(_split_run(fragment))
    return out


def _split_run(fragment: str) -> list[str]:
    """Break a separator-less run of known instrument names into fragments."""
    hits = []
    for word in sorted(INSTRUMENTS, key=len, reverse=True):
        for match in re.finditer(rf"\b{re.escape(word)}\b", fragment):
            if not any(a <= match.start() < b for a, b, _ in hits):
                hits.append((match.start(), match.end(), word))
    if len(hits) < 2:
        return [fragment]

    hits.sort()
    pieces, cursor = [], 0
    for start, end, _ in hits:
        # Keep any adjectives sitting between the previous name and this one.
        pieces.append(fragment[cursor:end].strip())
        cursor = end
    if (tail := fragment[cursor:].strip()):
        pieces[-1] = f"{pieces[-1]} {tail}".strip()
    return [p for p in pieces if p]


def _clean_instrument(fragment: str) -> str:
    """Strip leading filler until only the instrument is left.

    Applied repeatedly: "add 4 more tracks" sheds four separate words, and
    stopping after one would leave "4 more tracks" looking like a request
    for an instrument called that.
    """
    previous = None
    while previous != fragment:
        previous = fragment
        fragment = FILLER.sub("", fragment).strip()
    return fragment


def _interpret_with_rules(text: str, context: Context | None = None) -> Plan:
    lower = f" {text.lower()} "

    groove = grooves.for_style(lower)
    style = groove.name if groove.name != "straight" else ""

    # A request with no genre of its own inherits the session's, so an
    # "add a piano" cannot silently reset the groove to straight.
    if groove.name == "straight" and context and context.style:
        style = context.style
        groove = grooves.for_style(style)

    if any(phrase in lower for phrase in EVERYTHING):
        tracks = [TrackSpec(part=p, name=p, instrument="") for p in DEFAULT_BAND]
        return Plan(tracks=tracks, style=style, groove=groove.name)

    passthrough = bool(ADD_INTENT.search(lower))

    tracks: list[TrackSpec] = []
    for fragment in _split_requests(text):
        phrase = _clean_instrument(_strip_style(fragment, groove))
        if not phrase or phrase == style:
            continue

        known = _lookup(phrase)
        if known:
            part, instrument, name = known
        elif passthrough:
            # Unknown to us, but the user clearly asked for it. Their words
            # become the prompt verbatim; we only guess the guide to follow.
            part, instrument, name = _role_for(phrase), phrase, phrase
        else:
            continue

        tracks.append(
            TrackSpec(
                part=part,
                name="-".join(name.split())[:40],
                instrument=instrument,
            )
        )

    return Plan(
        tracks=tracks,
        style=style,
        groove=groove.name,
        notes="" if tracks else "Name the instruments you want, e.g. 'bass, drums and a Rhodes'.",
    )


def _strip_style(fragment: str, groove) -> str:
    """Remove genre words from an instrument fragment.

    The genre is already extracted into the arrangement's style, so leaving
    it attached here would name the track "guitar-bossa-nova" and push the
    words into the instrument description twice.
    """
    for word in sorted(groove.keywords, key=len, reverse=True):
        fragment = re.sub(rf"\b{re.escape(word)}\b", " ", fragment)
    return re.sub(r"\s+", " ", fragment).strip()


def _lookup(phrase: str) -> tuple[str, str, str] | None:
    """Match a fragment against the known-instrument table, typos included.

    The table exists to supply a *better* description than the user's bare
    word ("timpani" alone is a thin prompt) and to pin the right role. It is
    an enhancement, not a gate - anything it misses still gets generated.
    """
    import difflib

    if phrase in INSTRUMENTS:
        part, instrument = INSTRUMENTS[phrase]
        return part, instrument or "", phrase

    for word in sorted(INSTRUMENTS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(word)}\b", phrase):
            part, instrument = INSTRUMENTS[word]
            # Keep any extra adjectives the user typed around the match.
            extra = phrase.replace(word, "").strip()
            described = ", ".join(filter(None, [instrument or word, extra]))
            return part, described, phrase

    # 0.7, not the more cautious 0.8: real typos land lower than expected
    # ("tompany" -> "timpani" is only 0.71), and a miss here is worse than a
    # wrong guess — an unmatched phrase still generates, just with a
    # meaningless prompt and a guessed role.
    close = difflib.get_close_matches(phrase, INSTRUMENTS, n=1, cutoff=0.7)
    if close:
        part, instrument = INSTRUMENTS[close[0]]
        return part, instrument or close[0], close[0]
    return None
