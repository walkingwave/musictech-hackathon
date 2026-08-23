"""Stage 2: turn the analysis into MIDI for each backing part.

These arrangers are deterministic: the same session and seed always give
the same notes. Their job is to put the right notes at the right times so
Stable Audio 3 has a correct skeleton to work from. The MIDI is also
exported to the user, so it doubles as a DAW-editable starting point.

What they are NOT is a loop. An earlier version applied one groove pattern
to every bar at a fixed velocity and a fixed chord voicing, which is
exactly what sixteen bars of that sounds like: one bar, printed sixteen
times. Three things fix that, and every arranger here uses them:

  phrases      music is built in four-bar groups. `_phrase_plan` gives each
               bar an energy level and marks the last bar of each group, so
               parts thin out in an intro, lift into a chorus and turn the
               phrase around at its end.
  voice leading a chord is not one shape. `_voice_lead` picks the inversion
               closest to what was just played, so a progression moves by a
               semitone or two instead of leaping the same triad shape
               around the keyboard.
  humanising   `_Humaniser` nudges timing and velocity by a few
               milliseconds and a few steps. Seeded, so it is repeatable.

To add a part: write an `_arrange_<name>` function and register it in
ARRANGERS.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass

import librosa
import numpy as np
import pretty_midi

from . import melody
from .grooves import Groove, apply_swing, for_style
from .models import Analysis, Part
from .theory import chord_to_midi, parse_chord, scale_pitch_classes, transpose_diatonic

log = logging.getLogger(__name__)

# General MIDI program numbers, so exported MIDI opens with sane sounds.
GM_PROGRAMS = {
    "bass": 33,
    "piano": 0,
    "guitar": 25,
    "harmony": 52,
    "melody": 40,
    "mix": 0,
    "free": 89,
}
# finger bass, grand piano, steel acoustic guitar, choir aahs, violin

# General MIDI percussion note numbers.
DRUM_KICK, DRUM_SNARE, DRUM_HAT = 36, 38, 42
DRUM_OPEN_HAT, DRUM_RIDE, DRUM_CRASH = 46, 51, 49
DRUM_TOM_HI, DRUM_TOM_MID, DRUM_TOM_LO = 50, 47, 43

BARS_PER_PHRASE = 4


def arrange(
    part: Part,
    analysis: Analysis,
    vocal: np.ndarray,
    sr: int,
    style: str = "",
    seed: int = 0,
    voice_index: int = 0,
    voice_count: int = 1,
) -> pretty_midi.PrettyMIDI:
    """Build the MIDI for one part.

    `style` selects the rhythmic groove. It matters more than it looks:
    the guide track built from this MIDI is what fixes the output's rhythm,
    so a genre that is not expressed here cannot appear in the result no
    matter what the text prompt says.

    `seed` drives the variation and humanising, so regenerating a part with
    a new seed gives a genuinely different take rather than the same notes
    with a different timbre.

    `voice_index` / `voice_count` place this track among the others being
    generated with it — the second of two leads, the first of two comping
    parts — so `activity` can have them trade phrases and lay out for each
    other instead of all playing at once.

    `vocal` is only used by the harmony and melody arrangers.
    """
    if part not in ARRANGERS:
        raise ValueError(f"unknown part: {part}")
    active = activity(part, len(analysis.bars), voice_index, voice_count)
    return ARRANGERS[part](
        analysis,
        vocal,
        sr,
        for_style(style),
        _Humaniser(seed, part, voice_index, voice_count),
        active,
    )


# --- musical structure --------------------------------------------------


@dataclass(frozen=True)
class BarPlan:
    """Where one bar sits in the song, and how hard it should play."""

    index: int
    section: str  # intro | main | lift | outro
    energy: float  # 0..1, scales density and velocity
    phrase_pos: int  # 0-3 within its four-bar group
    is_turnaround: bool  # last bar of a phrase: fills and passing notes go here
    is_first: bool
    is_last: bool


def _phrase_plan(n_bars: int) -> list[BarPlan]:
    """Group bars into four-bar phrases and give the song an arc.

    Real arrangements do not start at full tilt and stay there. An intro
    holds back, the body sits at a working level, the phrase before the end
    lifts, and the last phrase resolves. Every arranger reads `energy` to
    decide how much to play, which is what stops all sixteen bars being
    identical.
    """
    phrases = max(1, (n_bars + BARS_PER_PHRASE - 1) // BARS_PER_PHRASE)
    plan: list[BarPlan] = []

    for index in range(n_bars):
        phrase = index // BARS_PER_PHRASE
        phrase_pos = index % BARS_PER_PHRASE

        # Short pieces have no room for an arc — treat everything as the body
        # rather than spending the only two phrases on an intro and an outro.
        if phrases <= 2:
            section, energy = "main", 0.85
        elif phrase == 0:
            section, energy = "intro", 0.55
        elif phrase == phrases - 1:
            section, energy = "outro", 0.7
        elif phrase == phrases - 2:
            section, energy = "lift", 1.0
        else:
            # A slow rise through the body rather than a flat plateau.
            section = "main"
            energy = 0.75 + 0.1 * min(1.0, phrase / max(1, phrases - 2))

        plan.append(
            BarPlan(
                index=index,
                section=section,
                energy=energy,
                phrase_pos=phrase_pos,
                is_turnaround=phrase_pos == BARS_PER_PHRASE - 1,
                is_first=index == 0,
                is_last=index == n_bars - 1,
            )
        )
    return plan

# --- who plays when ------------------------------------------------------

# Which arrangement job each part does. Space in a track comes from parts
# taking turns, and taking turns needs to know who is a soloist and who is
# accompaniment.
ROLES: dict[str, str] = {
    "drums": "rhythm",
    "bass": "rhythm",
    "piano": "comp",
    "guitar": "comp",
    "harmony": "comp",
    "melody": "lead",
    "free": "lead",
    "mix": "rhythm",  # a mix is the whole band; it never lays out
}


def activity(
    part: str,
    n_bars: int,
    voice_index: int = 0,
    voice_count: int = 1,
) -> list[bool]:
    """Which bars this part actually plays.

    A real record is not five instruments playing continuously for sixteen
    bars — that is what makes a generated arrangement sound like five takes
    stacked rather than a band. Players leave space: a soloist takes a
    chorus while the others comp, the piano lays out under someone else's
    line, the horns come back in together for the head.

    `voice_index` and `voice_count` say which of several same-role tracks
    this one is, so two leads trade phrases instead of both playing over
    each other for the whole tune.

    Returned per bar rather than per phrase because the arrangers work bar
    by bar, and because the same map gates the finished audio — the guide
    having rests is what the model follows, and the gate is what guarantees
    the space survives whatever the model does with the gaps.
    """
    plan = _phrase_plan(n_bars)
    phrases = max(1, (n_bars + BARS_PER_PHRASE - 1) // BARS_PER_PHRASE)
    role = ROLES.get(part, "lead")
    active: list[bool] = []

    for bar_plan in plan:
        phrase = bar_plan.index // BARS_PER_PHRASE
        first_phrase = phrase == 0
        last_phrase = phrase == phrases - 1

        if role == "rhythm":
            # The rhythm section is the floor: it holds the form together and
            # only drops out to mark the very start.
            playing = not (part == "drums" and bar_plan.is_first and voice_count > 1)
        elif role == "lead":
            # Heads in and out are played together; the middle is traded, one
            # phrase each in rotation. With a single lead this is just "plays
            # throughout", which is what a single lead should do.
            if voice_count <= 1 or phrases <= 2:
                playing = True
            elif first_phrase or last_phrase:
                playing = True
            else:
                playing = (phrase - 1) % voice_count == voice_index % voice_count
        else:  # comp
            # Comping is nearly continuous — it is what the soloist plays
            # over — but laying out for a phrase is the oldest trick there
            # is for making the next entry mean something. Staggered by
            # voice_index so two comping parts never rest together.
            if phrases <= 2:
                playing = True
            elif first_phrase and voice_count > 1:
                playing = False  # let the lead state the head over bass and drums
            else:
                playing = (phrase + voice_index) % 4 != 3

        active.append(playing)

    return active



class _Humaniser:
    """Small, seeded, repeatable deviations from the grid.

    A guide played exactly on the grid at exactly one velocity reads as a
    machine, and the model renders it that way. Pushing notes a few
    milliseconds either side and varying velocity by a few steps is the
    cheapest thing that makes a part sound played.

    Seeded per (seed, part) so the parts do not all shift in lockstep, and
    so the same request twice gives the same result.
    """

    def __init__(self, seed: int, part: str, voice_index: int = 0, voice_count: int = 1):
        self._random = random.Random(f"{seed}:{part}")
        # Carried here rather than added to every arranger signature: only
        # the melody uses it, to keep two leads out of the same register.
        self.voice_index = voice_index
        self.voice_count = voice_count

    def timing(self, spread: float = 0.012) -> float:
        """Seconds to nudge a note by."""
        return self._random.uniform(-spread, spread)

    def velocity(self, base: int, spread: int = 8) -> int:
        return int(np.clip(base + self._random.randint(-spread, spread), 1, 127))

    def chance(self, probability: float) -> bool:
        return self._random.random() < probability

    def pick(self, options):
        return self._random.choice(list(options))


def _new_midi(analysis: Analysis, part: Part) -> tuple[pretty_midi.PrettyMIDI, pretty_midi.Instrument]:
    midi = pretty_midi.PrettyMIDI(initial_tempo=analysis.bpm)
    instrument = pretty_midi.Instrument(
        program=GM_PROGRAMS.get(part, 0),
        is_drum=(part == "drums"),
        name=part,
    )
    midi.instruments.append(instrument)
    return midi, instrument


def _add(instrument: pretty_midi.Instrument, pitch: int, start: float, end: float, velocity: int) -> None:
    """Add a note, clamping pitch into the valid MIDI range."""
    instrument.notes.append(
        pretty_midi.Note(
            velocity=int(np.clip(velocity, 1, 127)),
            pitch=int(np.clip(pitch, 0, 127)),
            start=float(max(0.0, start)),
            end=float(max(end, start + 0.01)),
        )
    )


def _voice_lead(triad: list[int], previous: list[int] | None) -> list[int]:
    """Pick the inversion of `triad` that moves least from `previous`.

    Playing every chord in root position makes a progression lurch around
    the keyboard, and each chord reads as a separate event rather than as
    part of a line. Voicing each one near the last is what a keyboard
    player does without thinking, and it is most of the difference between
    "chords" and "a chord progression".
    """
    if not previous:
        return sorted(triad)

    target = sum(previous) / len(previous)
    best, best_distance = sorted(triad), None
    for rotation in range(len(triad)):
        # Rotate by lifting the lowest notes an octave: the same chord,
        # voiced higher each time.
        voicing = sorted(triad[rotation:] + [p + 12 for p in triad[:rotation]])
        for shift in (-12, 0, 12):
            candidate = [p + shift for p in voicing]
            distance = abs(sum(candidate) / len(candidate) - target)
            if best_distance is None or distance < best_distance:
                best, best_distance = candidate, distance
    return best


def _seventh(chord: str, triad: list[int]) -> int:
    """The chord's seventh, for the colour a plain triad cannot give.

    Minor and dominant sevenths are a whole tone below the octave; major
    sevenths a semitone. Getting this wrong is the difference between jazzy
    and sour, so it follows the chord quality rather than being fixed.
    """
    _, quality = parse_chord(chord)
    root = triad[0]
    return root + (11 if quality == "maj" else 10)


# --- bass ---------------------------------------------------------------


def _arrange_bass(
    analysis: Analysis, vocal: np.ndarray, sr: int, groove: Groove, human: _Humaniser,
    active: list[bool],
) -> pretty_midi.PrettyMIDI:
    """Bass notes where the groove puts them, on chord tones.

    Two things lift this above a repeating pattern: the phrase plan thins
    the line out in the intro and drives it in the lift, and the last bar of
    each phrase walks a passing note into the next chord instead of sitting
    on the root — which is what a bass player does to join two phrases.
    """
    midi, instrument = _new_midi(analysis, "bass")
    beat = analysis.seconds_per_beat
    plan = _phrase_plan(len(analysis.bars))
    scale = scale_pitch_classes(analysis.key, analysis.mode)

    for bar, bar_plan in zip(analysis.bars, plan):
        if not active[bar_plan.index]:
            continue  # laying out: the space is the arrangement
        triad = chord_to_midi(bar.chord, octave=2)
        for position, tone in groove.bass:
            start = bar.start + apply_swing(position, groove.swing) * beat
            if start >= bar.end:
                continue
            # Offbeat notes are the first thing to go when the energy is
            # down, so an intro is the spine of the line rather than all of it.
            if position % 1.0 and bar_plan.energy < 0.7 and not human.chance(0.4):
                continue

            pitch = triad[tone % len(triad)]
            # An octave drop on the downbeat of a lift, where a bass player
            # would reach for the low string.
            if bar_plan.section == "lift" and position == 0.0 and human.chance(0.4):
                pitch -= 12

            velocity = human.velocity(int(80 + 25 * bar_plan.energy))
            _add(
                instrument,
                pitch,
                start + human.timing(),
                min(start + beat * 0.9, bar.end),
                velocity=velocity,
            )

        # Walk into the next chord across the turnaround.
        next_bar = analysis.bars[bar_plan.index + 1] if not bar_plan.is_last else None
        if bar_plan.is_turnaround and next_bar and bar_plan.energy > 0.6:
            target = chord_to_midi(next_bar.chord, octave=2)[0]
            approach = _approach_note(triad[0], target, scale)
            start = bar.end - beat * 0.5
            _add(instrument, approach, start, bar.end, velocity=human.velocity(90))

    return midi


def _approach_note(current: int, target: int, scale: list[int]) -> int:
    """A scale tone between two roots, or a chromatic step if they are close.

    This is the note that makes a bass line lead somewhere. A leading tone a
    semitone below the target is the strongest, and works even when the two
    chords are far apart.
    """
    if abs(target - current) <= 2:
        return target - 1
    direction = 1 if target > current else -1
    candidate = target - direction
    # Prefer a note in the key; fall back to the chromatic approach.
    for offset in range(3):
        probe = candidate - direction * offset
        if probe % 12 in scale:
            return probe
    return target - direction


# --- chordal parts ------------------------------------------------------


def _comp(
    analysis: Analysis,
    groove: Groove,
    part: Part,
    octave: int,
    velocity: int,
    human: _Humaniser,
    active: list[bool],
) -> pretty_midi.PrettyMIDI:
    """Chord comping shared by piano and guitar — only the register differs.

    The rhythm comes from the groove, which is what lets a bossa comp land
    on the offbeats while a rock comp sits on 1 and 3. On top of that the
    phrase plan decides how much of the pattern is actually played, the
    voicing follows the previous chord instead of resetting to root
    position, and a seventh is added where the energy is high enough to
    carry it.
    """
    midi, instrument = _new_midi(analysis, part)
    beat = analysis.seconds_per_beat
    plan = _phrase_plan(len(analysis.bars))
    previous: list[int] | None = None

    for bar, bar_plan in zip(analysis.bars, plan):
        if not active[bar_plan.index]:
            continue  # laying out: the space is the arrangement
        triad = chord_to_midi(bar.chord, octave=octave)
        voicing = _voice_lead(triad, previous)
        previous = voicing

        # Colour, not decoration: a seventh in the lift and on turnarounds is
        # what stops eight bars of plain triads sounding like an exercise.
        notes = list(voicing)
        if bar_plan.energy >= 0.85 and (bar_plan.is_turnaround or human.chance(0.35)):
            notes.append(_seventh(bar.chord, voicing))

        for i, (position, length) in enumerate(groove.comp):
            start = bar.start + apply_swing(position, groove.swing) * beat
            if start >= bar.end:
                continue
            # Thin the comp when the section is quiet, keeping the first stab
            # of the bar so the harmony still lands.
            if i and bar_plan.energy < 0.7 and not human.chance(0.35):
                continue

            end = min(start + length * beat, bar.end)
            stab_velocity = human.velocity(int(velocity * (0.75 + 0.35 * bar_plan.energy)))
            # A chord is not struck as one event; spreading it a few
            # milliseconds is the difference between a strum and a stab.
            spread = 0.006 if part == "guitar" else 0.003
            for offset, pitch in enumerate(notes):
                _add(
                    instrument,
                    pitch,
                    start + offset * spread + human.timing(0.008),
                    end,
                    velocity=stab_velocity - offset,
                )

    return midi


def _arrange_piano(analysis, vocal, sr, groove, human, active):
    return _comp(analysis, groove, "piano", octave=4, velocity=75, human=human, active=active)


def _arrange_guitar(analysis, vocal, sr, groove, human, active):
    # An octave below the piano, where a guitar actually voices chords.
    return _comp(analysis, groove, "guitar", octave=3, velocity=70, human=human, active=active)


def _arrange_free(analysis: Analysis, vocal: np.ndarray, sr: int, groove: Groove, human: _Humaniser, active: list[bool]):
    """A sustained chord bed - harmony and tempo only, no groove.

    Every other arranger encodes how its instrument plays. This one
    deliberately does not: one held triad per bar gives Stable Audio 3 the
    key and the bar grid to follow while leaving the rhythm entirely to the
    prompt. It is what lets an instrument we know nothing about still come
    back in time and in key.
    """
    midi, instrument = _new_midi(analysis, "free")
    plan = _phrase_plan(len(analysis.bars))
    previous: list[int] | None = None
    for bar, bar_plan in zip(analysis.bars, plan):
        if not active[bar_plan.index]:
            continue  # laying out: the space is the arrangement
        voicing = _voice_lead(chord_to_midi(bar.chord, octave=3), previous)
        previous = voicing
        for pitch in voicing:
            _add(instrument, pitch, bar.start, bar.end, velocity=human.velocity(int(55 + 25 * bar_plan.energy), 4))
    return midi


# --- drums --------------------------------------------------------------


def _arrange_drums(
    analysis: Analysis, vocal: np.ndarray, sr: int, groove: Groove, human: _Humaniser,
    active: list[bool],
) -> pretty_midi.PrettyMIDI:
    """Kit pattern taken from the groove, played as an arrangement.

    This is the part where genre is most audible, and where a fixed
    backbeat did the most damage: a bossa nova request rendered with kick
    on 1 and 3 and snare on 2 and 4 simply is not bossa nova, whatever the
    text prompt asks for.

    It is also where a loop is most obvious. A drummer marks the start of a
    phrase with a crash, drops ghost notes between the backbeats, opens the
    hat before the turnaround and fills into the next phrase. All four are
    here, driven by the phrase plan rather than sprinkled at random.
    """
    midi, instrument = _new_midi(analysis, "drums")
    beat = analysis.seconds_per_beat
    plan = _phrase_plan(len(analysis.bars))

    for bar, bar_plan in zip(analysis.bars, plan):
        if not active[bar_plan.index]:
            continue  # laying out: the space is the arrangement

        def hits(positions, note, velocity, length=0.1):
            for position in positions:
                start = bar.start + apply_swing(position, groove.swing) * beat
                if start < bar.end:
                    _add(
                        instrument,
                        note,
                        start + human.timing(0.008),
                        start + length,
                        velocity=human.velocity(velocity),
                    )

        # The fill replaces the second half of the bar, so it lands instead of
        # the pattern rather than on top of it.
        filling = bar_plan.is_turnaround and bar_plan.energy >= 0.7 and not bar_plan.is_last

        kick = [p for p in groove.kick if not (filling and p >= 2.0)]
        snare = [p for p in groove.snare if not (filling and p >= 2.0)]
        hits(kick, DRUM_KICK, int(88 + 14 * bar_plan.energy))
        hits(snare, DRUM_SNARE, int(80 + 14 * bar_plan.energy))

        # Ghost notes: quiet snares between the backbeats. Barely audible on
        # their own, and the whole difference between a drum machine and a
        # drummer when they are there.
        if bar_plan.energy >= 0.75 and not filling:
            for position in (1.75, 3.75):
                if human.chance(0.4):
                    hits([position], DRUM_SNARE, 38)

        for position in groove.hat:
            if filling and position >= 2.0:
                continue
            start = bar.start + apply_swing(position, groove.swing) * beat
            if start >= bar.end:
                continue
            on_beat = abs(position % 1.0) < 1e-6
            # Half-time hats in the quietest sections: the pattern is still
            # the groove's, just played on the beats.
            if bar_plan.energy < 0.6 and not on_beat:
                continue
            note, velocity = DRUM_HAT, (70 if on_beat else 55)
            # An open hat just before the turnaround, the standard signal
            # that a phrase is about to end.
            if bar_plan.is_turnaround and position == max(groove.hat):
                note, velocity = DRUM_OPEN_HAT, 78
            _add(
                instrument,
                note,
                start + human.timing(0.006),
                start + 0.05,
                velocity=human.velocity(int(velocity * (0.7 + 0.3 * bar_plan.energy)), 6),
            )

        if filling:
            _fill(instrument, bar, beat, human)

        # A crash on the first downbeat of each new section marks the seam.
        if bar_plan.phrase_pos == 0 and bar_plan.section in ("lift", "main") and not bar_plan.is_first:
            _add(instrument, DRUM_CRASH, bar.start, bar.start + 0.4, velocity=human.velocity(96))

    return midi


def _fill(instrument, bar, beat: float, human: _Humaniser) -> None:
    """Two beats of toms into the next phrase.

    Kept to the second half of the turnaround bar and to three tom voices:
    long enough to signal the phrase change, short enough that it does not
    become the point of the bar.
    """
    figures = (
        ((2.0, DRUM_TOM_HI), (2.5, DRUM_TOM_HI), (3.0, DRUM_TOM_MID), (3.5, DRUM_TOM_LO)),
        ((2.0, DRUM_SNARE), (2.25, DRUM_SNARE), (2.5, DRUM_TOM_MID), (3.0, DRUM_TOM_LO), (3.5, DRUM_TOM_LO)),
        ((2.5, DRUM_TOM_MID), (3.0, DRUM_TOM_MID), (3.25, DRUM_TOM_LO), (3.5, DRUM_TOM_LO)),
    )
    for i, (position, note) in enumerate(human.pick(figures)):
        start = bar.start + position * beat
        if start >= bar.end:
            continue
        # Fills accelerate into the downbeat rather than sitting flat.
        _add(instrument, note, start + human.timing(0.006), start + 0.12, velocity=human.velocity(78 + i * 4))


# --- harmony ------------------------------------------------------------


def _arrange_harmony(
    analysis: Analysis, vocal: np.ndarray, sr: int, groove: Groove, human: _Humaniser,
    active: list[bool],
) -> pretty_midi.PrettyMIDI:
    """Track the vocal's pitch, then sing a diatonic third above it.

    This is the part most directly derived from the user's own melody,
    which makes it the strongest demo moment — and the most fragile, since
    it depends on clean monophonic pitch tracking.

    With nothing to harmonise — a session started from a prompt rather than
    a recording, or a vocal too quiet to track — it falls back to a
    sustained chord bed. Returning no notes at all produced a silent guide,
    and a silent guide handed to the model comes back as a silent stem: the
    track appeared on the timeline and played nothing.
    """
    midi, instrument = _new_midi(analysis, "harmony")
    mono = librosa.to_mono(vocal) if vocal.ndim > 1 else vocal

    # Pitch tracking on near-silence is both pointless and slow.
    if mono.size and float(np.abs(mono).max()) > 1e-4:
        for note in melody.track(mono, sr):
            harmonized = transpose_diatonic(
                note.pitch, steps=2, key=analysis.key, mode=analysis.mode
            )
            _add(instrument, harmonized, note.start, note.end, velocity=80)

    if not instrument.notes:
        log.info("harmony: no melody to follow, using a sustained chord bed")
        plan = _phrase_plan(len(analysis.bars))
        previous: list[int] | None = None
        for bar, bar_plan in zip(analysis.bars, plan):
            if not active[bar_plan.index]:
                continue  # laying out: the space is the arrangement
            # A pad swells rather than switching: overlapping the bars by a
            # beat is what makes a string or choir line breathe.
            voicing = _voice_lead(chord_to_midi(bar.chord, octave=4), previous)
            previous = voicing
            end = min(bar.end + analysis.seconds_per_beat * 0.5, analysis.duration)
            for pitch in voicing:
                _add(instrument, pitch, bar.start, end, velocity=human.velocity(int(55 + 25 * bar_plan.energy), 5))

    return midi


# --- melody -------------------------------------------------------------

# Rhythms a lead line is built from, in (beat, length) pairs. Each is a
# complete one-bar idea with space in it — a melody that plays continuously
# has no shape, and the rests are what let the phrase breathe.
MELODY_FIGURES = (
    ((0.0, 1.0), (1.0, 0.5), (1.5, 1.5), (3.0, 1.0)),
    ((0.0, 1.5), (1.5, 0.5), (2.0, 2.0)),
    ((0.5, 0.5), (1.0, 1.0), (2.0, 0.5), (2.5, 1.5)),
    ((0.0, 2.0), (2.5, 1.5)),
    ((0.0, 0.5), (0.5, 0.5), (1.0, 1.0), (2.0, 1.0), (3.0, 1.0)),
)

# Contours in scale degrees relative to the chord tone the phrase starts on.
# A melody goes somewhere and comes back; these are the shapes that do that.
MELODY_CONTOURS = (
    (0, 1, 2, 1),
    (0, 2, 1, -1),
    (0, -1, 1, 2),
    (2, 1, 0, -1),
    (0, 1, -1, 0),
)


def _arrange_melody(
    analysis: Analysis, vocal: np.ndarray, sr: int, groove: Groove, human: _Humaniser,
    active: list[bool],
) -> pretty_midi.PrettyMIDI:
    """A lead line over the chord grid: the part that carries the tune.

    Distinct from `harmony`, which shadows an existing vocal, and from the
    chordal parts, which state the harmony. This one is what a violin, a
    flute or a lead synth actually plays, and without it every melodic
    instrument was being arranged as a pad.

    Built the way a phrase is written rather than a bar generated: one motif
    per four-bar phrase, restated over each bar's chord, varied at the
    turnaround and resolved onto a chord tone at the end. Repetition with
    variation is what makes a melody sound composed instead of sampled.
    """
    midi, instrument = _new_midi(analysis, "melody")
    beat = analysis.seconds_per_beat
    plan = _phrase_plan(len(analysis.bars))
    key, mode = analysis.key, analysis.mode

    figure = human.pick(MELODY_FIGURES)
    contour = human.pick(MELODY_CONTOURS)

    for bar, bar_plan in zip(analysis.bars, plan):
        if not active[bar_plan.index]:
            continue  # laying out: the space is the arrangement
        # A new idea each phrase, so a long piece does not ride one motif all
        # the way through — but the same idea within the phrase, so it reads
        # as a phrase and not as four unrelated bars.
        if bar_plan.phrase_pos == 0:
            figure = human.pick(MELODY_FIGURES)
            contour = human.pick(MELODY_CONTOURS)

        # Leave the first bar of an intro empty: a lead that enters with
        # everything else has no entrance.
        if bar_plan.section == "intro" and bar_plan.is_first:
            continue

        # Two leads in the same octave fight; the second sits a register
        # lower so a sax and a guitar occupy different space in the mix.
        octave = 5 if human.voice_index % 2 == 0 else 4
        triad = chord_to_midi(bar.chord, octave=octave)
        anchor = triad[0] if bar_plan.phrase_pos % 2 == 0 else triad[min(2, len(triad) - 1)]

        for i, (position, length) in enumerate(figure):
            start = bar.start + apply_swing(position, groove.swing) * beat
            if start >= bar.end:
                continue
            # Thin the line where the section is quiet, keeping the note that
            # starts the bar.
            if i and bar_plan.energy < 0.65 and not human.chance(0.5):
                continue

            step = contour[i % len(contour)]
            pitch = transpose_diatonic(anchor, steps=step, key=key, mode=mode)

            # The turnaround varies the idea rather than repeating it, and
            # the final bar resolves onto a chord tone so the line ends
            # rather than stopping.
            if bar_plan.is_turnaround and human.chance(0.5):
                pitch = transpose_diatonic(pitch, steps=1, key=key, mode=mode)
            if bar_plan.is_last and i == len(figure) - 1:
                pitch = triad[0]

            end = min(start + length * beat, bar.end)
            _add(
                instrument,
                pitch,
                start + human.timing(0.015),
                end,
                velocity=human.velocity(int(72 + 30 * bar_plan.energy)),
            )

    return midi


# --- full mix -----------------------------------------------------------


def _arrange_mix(
    analysis: Analysis, vocal: np.ndarray, sr: int, groove: Groove, human: _Humaniser,
    active: list[bool],
) -> pretty_midi.PrettyMIDI:
    """The whole band in one guide: drums, bass, chords and a lead line.

    Stable Audio 3 is at its best rendering a full arrangement in one pass —
    an ensemble is what most of its training data is. Generating a song as
    four isolated stems and stacking them is fighting that; this part hands
    it the whole skeleton at once and asks for the record.

    Each layer is the same arranger the solo parts use, so a mix is exactly
    the parts that would have been generated separately, playing together
    and sharing one phrase plan.
    """
    midi = pretty_midi.PrettyMIDI(initial_tempo=analysis.bpm)
    layers = ("drums", "bass", "piano", "melody")
    for i, part in enumerate(layers):
        # Inside a mix the layers still take turns with each other — the
        # comping lays out under the lead, the lead trades with itself — so
        # a single generated track has the same space a real one does.
        layer_active = activity(part, len(analysis.bars), i, len(layers))
        layer = ARRANGERS[part](
            analysis, vocal, sr, groove, _Humaniser(human_seed(human), part), layer_active
        )
        midi.instruments.extend(layer.instruments)
    return midi


def human_seed(human: _Humaniser) -> int:
    """A stable per-mix seed, so the layers vary against each other but the
    mix as a whole is still reproducible."""
    return human._random.randint(0, 2**31 - 1)  # noqa: SLF001 - same module


ARRANGERS = {
    "bass": _arrange_bass,
    "piano": _arrange_piano,
    "guitar": _arrange_guitar,
    "drums": _arrange_drums,
    "harmony": _arrange_harmony,
    "melody": _arrange_melody,
    "mix": _arrange_mix,
    "free": _arrange_free,
}
