"""Rhythmic patterns per genre.

Why this exists: the guide track dictates rhythm. Stable Audio 3 follows
the guide's note placement closely — that is the entire reason the output
lands in time — which means a guide built from a rock backbeat produces a
rock feel no matter what the text prompt says. Asking for "bossa nova" and
getting a straight backbeat is not the model ignoring the prompt; it is the
guide overriding it.

So genre has to be expressed as *note placement*, not just as words. Each
groove below defines where the kick, snare, bass notes and chord stabs
actually fall.

Positions are in beats within a 4/4 bar, zero-indexed: 0 is the downbeat,
1.5 is the "and" of two. Chord tones are indices into the triad, so 0 is
the root, 1 the third, 2 the fifth.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Groove:
    name: str
    description: str

    kick: tuple[float, ...]
    snare: tuple[float, ...]
    hat: tuple[float, ...]

    # (beat, chord-tone index)
    bass: tuple[tuple[float, int], ...]

    # (beat, duration in beats) for sustained chord comping
    comp: tuple[tuple[float, float], ...]

    # How much to delay every offbeat, as a fraction of a beat. 0 is
    # straight; ~0.08 is the long-short lilt of swung eighths.
    swing: float = 0.0

    # Words that select this groove, matched against the user's style text.
    keywords: tuple[str, ...] = field(default_factory=tuple)


EIGHTHS = (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5)
SIXTEENTHS = tuple(i * 0.25 for i in range(16))

STRAIGHT = Groove(
    name="straight",
    description="rock/pop backbeat",
    kick=(0.0, 2.0),
    snare=(1.0, 3.0),
    hat=EIGHTHS,
    bass=((0.0, 0), (1.0, 0), (2.0, 2), (3.0, 0)),
    comp=((0.0, 2.0), (2.0, 2.0)),
    keywords=("rock", "pop", "indie", "anthem", "punk"),
)

BOSSA = Groove(
    name="bossa",
    description="bossa nova / samba, syncopated with anticipations",
    # Surdo-style pulse rather than a backbeat; the snare is a rim click
    # falling off the beat, which is what gives bossa its lean.
    kick=(0.0, 1.5, 2.0, 3.5),
    snare=(1.0, 2.5),
    hat=EIGHTHS,
    # The defining feature: the bass anticipates, landing on the "and" of
    # two rather than squarely on three.
    bass=((0.0, 0), (1.5, 2), (2.0, 0), (3.5, 2)),
    # Comping pushes ahead of the beat instead of sitting on 1 and 3.
    comp=((0.5, 1.0), (1.5, 0.5), (2.5, 1.0), (3.5, 0.5)),
    keywords=("bossa", "nova", "samba", "latin", "brazil", "brazilian", "tropical"),
)

SWING = Groove(
    name="swing",
    description="jazz swing with a walking bass",
    kick=(0.0,),
    snare=(1.0, 3.0),
    hat=(0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5),
    # Walking: a note on every beat, moving through the chord.
    bass=((0.0, 0), (1.0, 1), (2.0, 2), (3.0, 1)),
    comp=((0.5, 1.0), (2.5, 1.0)),
    swing=0.08,
    keywords=("jazz", "swing", "bebop", "lounge", "big band"),
)

FUNK = Groove(
    name="funk",
    description="syncopated funk, sixteenth-note feel",
    kick=(0.0, 0.75, 2.5),
    snare=(1.0, 3.0),
    hat=SIXTEENTHS,
    bass=((0.0, 0), (0.75, 0), (1.5, 2), (2.5, 0), (3.25, 2)),
    comp=((1.0, 0.25), (2.75, 0.25), (3.5, 0.5)),
    keywords=("funk", "funky", "groove", "disco", "soul", "motown"),
)

REGGAE = Groove(
    name="reggae",
    description="one-drop, offbeat skank",
    # One drop: nothing on beat 1, the weight lands on three.
    kick=(2.0,),
    snare=(2.0,),
    hat=EIGHTHS,
    bass=((0.0, 0), (1.5, 2), (2.5, 0)),
    # Skank chords on the offbeats only.
    comp=((1.0, 0.5), (2.0, 0.5), (3.0, 0.5)),
    keywords=("reggae", "dub", "ska", "roots"),
)

BALLAD = Groove(
    name="ballad",
    description="sparse and slow, sustained chords",
    kick=(0.0,),
    snare=(2.0,),
    hat=(0.0, 1.0, 2.0, 3.0),
    bass=((0.0, 0), (2.0, 2)),
    comp=((0.0, 4.0),),
    keywords=("ballad", "slow", "ambient", "cinematic", "dreamy", "sad", "gentle", "lo-fi", "lofi"),
)

HOUSE = Groove(
    name="house",
    description="four-on-the-floor",
    kick=(0.0, 1.0, 2.0, 3.0),
    snare=(1.0, 3.0),
    hat=(0.5, 1.5, 2.5, 3.5),
    bass=((0.0, 0), (0.5, 0), (1.5, 0), (2.0, 0), (2.5, 0), (3.5, 2)),
    comp=((0.5, 0.5), (2.5, 0.5)),
    keywords=("house", "techno", "edm", "dance", "club", "four on the floor"),
)

WALTZ = Groove(
    name="waltz",
    description="three-feel, oom-pah-pah",
    kick=(0.0,),
    snare=(1.0, 2.0),
    hat=(0.0, 1.0, 2.0),
    bass=((0.0, 0), (1.0, 2), (2.0, 2)),
    comp=((1.0, 1.0), (2.0, 1.0)),
    keywords=("waltz", "3/4", "jig"),
)

ALL = (BOSSA, SWING, FUNK, REGGAE, HOUSE, WALTZ, BALLAD, STRAIGHT)


def for_style(style: str) -> Groove:
    """Pick the groove whose keywords best match the user's style text.

    Falls back to a straight backbeat, which is the least surprising thing
    to hear when we genuinely cannot tell.
    """
    text = (style or "").lower()
    for groove in ALL:  # STRAIGHT is last, so specific genres win
        if any(word in text for word in groove.keywords):
            return groove
    return STRAIGHT


def apply_swing(beat: float, swing: float) -> float:
    """Delay offbeat eighths to swing them; leave downbeats alone."""
    if swing <= 0:
        return beat
    return beat + swing if abs(beat % 1.0 - 0.5) < 1e-6 else beat
