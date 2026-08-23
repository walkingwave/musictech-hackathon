"""Per-part text prompts for Stable Audio 3.

Two things matter here:

1. Tempo and key go in the prompt even though the guide track already
   encodes them. Reinforcing them reduces drift.
2. The isolation clause ("solo instrument, no drums, no vocals") is not
   optional. Left to itself the model renders a full mix, and we need a
   single stem.
"""

from __future__ import annotations

from .models import Analysis, Part

# What the instrument should sound like, before the user's own style text.
# Split into the instrument itself and the room it was recorded in, because
# the two are chosen at different times: the instrument is per part, the room
# has to be shared by the whole arrangement. Leaving "dry room" baked into the
# drum default while the arrangement asks for a roomy 70s live take hands the
# model two contradictory recordings and it picks one at random per stem —
# which is exactly the incoherence the shared `production` clause exists to
# remove.
#
# Harmony asks for a choir pad rather than "backing vocal harmony". SA3's
# training data (AudioSparx + Freesound) is thin on isolated vocal stems
# and rich in pads, and measured against a correct A minor guide the vocal
# phrasing came back with pitch classes outside the key while the choir
# phrasing stayed in it.
INSTRUMENT_PHRASES: dict[Part, str] = {
    "bass": "warm fingered electric bass guitar, clean low end",
    "piano": "acoustic grand piano chords",
    "guitar": "clean electric guitar chords, warm amp",
    "drums": "tight acoustic drum kit, punchy kick and snare",
    "harmony": "warm choir pad, sustained aahs, soft attack",
    # `free` always carries a user-supplied instrument, so it needs no
    # default of its own - and inventing one would compete with theirs.
    "free": "",
}

# Used only when the arrangement has no production description of its own,
# so a single stem generated on its own still sounds recorded rather than
# synthetic.
DEFAULT_ROOM: dict[Part, str] = {
    "bass": "dry DI signal",
    "piano": "close-miked, natural room",
    "guitar": "light room",
    "drums": "dry room",
    "harmony": "close-miked",
    "free": "",
}

# What must not appear in the stem. Everything except the target part.
ISOLATION: dict[Part, str] = {
    "bass": "solo bass only, no drums, no vocals, no piano",
    "piano": "solo piano only, no drums, no vocals, no bass",
    "guitar": "solo guitar only, no drums, no vocals, no bass",
    "drums": "drums only, no melody, no vocals, no bass",
    "harmony": "single sustained layer, no drums, no bass, no percussion",
    "free": "solo instrument, one layer only, no drums, no vocals",
}


def build(
    part: Part,
    analysis: Analysis,
    style: str = "",
    instrument: str = "",
    production: str = "",
) -> str:
    """Compose the full prompt for one part.

    `style` is free text about the music, e.g. "bossa nova" or "gritty 70s funk".

    `instrument` **replaces** the default instrument phrase rather than
    adding to it. That matters: asking for a wah bass while the default
    still says "dry DI signal" hands the model two contradictory
    descriptions of the same sound, and the default usually wins. When the
    user names an instrument, it should be the only one described.

    `production` describes the *recording* rather than the music — room,
    mics, era, tape, mix character — and is shared by every part of the
    arrangement. Each stem is a separate model call with no memory of the
    others, so this shared clause is what makes them sound like one band in
    one room instead of four unrelated takes in the same key. It goes after
    the style and before the isolation clause: late enough not to fight the
    instrument description, early enough not to be truncated.
    """
    room = production.strip() or DEFAULT_ROOM[part]
    pieces = [
        instrument.strip() or INSTRUMENT_PHRASES[part],
        style.strip(),
        f"{round(analysis.bpm)} BPM",
        f"{analysis.key} {analysis.mode}",
        room,
        ISOLATION[part],
    ]
    return ", ".join(piece for piece in pieces if piece)
