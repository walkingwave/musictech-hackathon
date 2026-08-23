"""The pipeline: vocal in, backing stems out.

This module is the one place that knows the order of operations. Every
other backend module does exactly one stage and knows nothing about the
others, so this is where to look to understand the flow.

    analyze_vocal()  stage 1
    generate_stem()  stages 2-5, for one part
"""

from __future__ import annotations

import logging
import math
import random

import numpy as np
import pretty_midi
import soundfile as sf

from . import align, arrange, config, grooves, prompts, render_guide, sa3_backend
from .analysis import analyze
from .config import SAMPLE_RATE
from .models import Analysis, Arrangement, Bar, Part, StemResult
from .session import Session
from .theory import note_to_pitch_class, pitch_class_to_note

log = logging.getLogger(__name__)


def analyze_vocal(vocal_path) -> tuple[Session, Analysis]:
    """Stage 1. Create a session from an audio file and analyze it."""
    audio, sr = sf.read(vocal_path, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    if sr != SAMPLE_RATE:
        import librosa

        audio = librosa.resample(audio, orig_sr=sr, target_sr=SAMPLE_RATE)
        sr = SAMPLE_RATE

    session = Session.create(audio, sr)
    analysis = analyze(audio, sr)
    session.save_analysis(analysis)

    log.info(
        "session %s: %.1f BPM, %s %s, %d bars",
        session.id, analysis.bpm, analysis.key, analysis.mode, len(analysis.bars),
    )
    return session, analysis


def _extend_analysis(analysis: Analysis, target_bars: int, start_bar: int) -> Analysis:
    """Tile the analyzed chord grid into a longer (or offset) bar grid.

    The input vocal is short; a backing track wants to be longer and, for a
    section regenerate, to start partway through the progression. We repeat
    the detected chords from `start_bar` for `target_bars` bars, laying them
    on a fresh timeline that begins at 0 (the guide has no lead-in silence).
    """
    chords = [b.chord for b in analysis.bars] or ["C"]
    n = len(chords)
    spb = analysis.seconds_per_bar
    bars = [
        Bar(
            index=i,
            start=i * spb,
            end=(i + 1) * spb,
            chord=chords[(start_bar + i) % n],
        )
        for i in range(target_bars)
    ]
    return Analysis(
        bpm=analysis.bpm,
        downbeat_offset_s=0.0,
        key=analysis.key,
        mode=analysis.mode,
        duration=target_bars * spb,
        bars=bars,
    )


def _resolve_arrangement(
    session: Session,
    style: str | None,
    bars: int | None,
    analysis: Analysis,
    production: str | None = None,
    seed: int | None = None,
) -> tuple[str, int, str, int]:
    """Settle what this part must share with the others.

    Inherit whatever the session already agreed on; anything passed
    explicitly wins and is written back, so a later change of mind moves
    the whole arrangement instead of leaving one part out of step.

    Returns (style, bars, production, tone_seed). The last two are the
    cohesion levers: the same recording description and the same seed on
    every part, so four separate model calls sound like one session rather
    than four unrelated records that happen to share a key.
    """
    arrangement = session.arrangement
    changed = False

    if style is None:
        style = arrangement.style
    elif style != arrangement.style:
        arrangement.style = style
        changed = True

    if bars is None:
        # Fall back to the analysed length, so the first part sets the
        # length and every later part matches it.
        bars = arrangement.bars or len(analysis.bars) or 8
    if bars != arrangement.bars:
        arrangement.bars = bars
        changed = True

    if production is None:
        production = arrangement.production
    elif production != arrangement.production:
        arrangement.production = production
        changed = True

    # The first part to be generated fixes the seed for everything after it.
    # A caller that passes its own seed (regenerating one clip, hunting for a
    # better take) is asking for variation, so it does not re-pin the shared
    # one.
    if arrangement.tone_seed is None:
        arrangement.tone_seed = seed if seed is not None else random.randint(0, 2**31 - 1)
        changed = True

    if changed:
        session.save_arrangement(arrangement)
        log.info(
            "arrangement: style=%r groove=%s bars=%d production=%r",
            style, grooves.for_style(style).name, bars, production,
        )

    return style, bars, production, arrangement.tone_seed


def track_name(session: Session, part: Part, requested: str | None) -> str:
    """A filesystem-safe track name, unique within the session.

    Several tracks can share a part - a xylophone and a piano are both
    "piano" to the arranger - so names are what keep their files apart.
    """
    base = "".join(c for c in (requested or part).lower() if c.isalnum() or c in "-_ ")
    base = "-".join(base.split()) or part

    existing = set(session.to_dict().get("stems", {}))
    if base not in existing:
        return base[:40]
    for n in range(2, 100):
        candidate = f"{base[:36]}-{n}"
        if candidate not in existing:
            return candidate
    return base[:40]


def generate_stem(
    session: Session,
    part: Part,
    style: str | None = None,
    noise: float | None = None,
    backend: str | None = None,
    seed: int | None = None,
    bars: int | None = None,
    start_bar: int = 0,
    name: str | None = None,
    instrument: str = "",
    production: str | None = None,
) -> StemResult:
    """Stages 2-5 for one part: arrange, render a guide, generate, align.

    The guide track is what makes the output land in time and in key. It
    carries the rhythm and harmony through the model's noised latent, so
    what comes back has the right skeleton and a real instrument's timbre.

    `style` and `bars` default to the session's arrangement, so a part
    added later shares the groove and length of the parts already there.
    Passing either explicitly overrides it *and* re-pins the arrangement,
    which is what makes "actually, make it reggae" change the whole
    session rather than producing one reggae part among bossa ones.

    `noise` defaults per part - see config.PART_NOISE.
    """
    analysis = session.analysis
    vocal, sr = session.read_vocal()
    noise = noise if noise is not None else config.default_noise(part)

    style, bars, production, tone_seed = _resolve_arrangement(
        session, style, bars, analysis, production, seed
    )
    # No seed given means "another part of the same record", so it reuses the
    # arrangement seed rather than rolling a fresh one and drifting away from
    # the parts already generated.
    seed = seed if seed is not None else tone_seed
    name = name or part

    # A longer target length (or a section offset) works over a tiled copy of
    # the chord grid; the original vocal analysis stays untouched on disk.
    work = _extend_analysis(analysis, bars, start_bar)

    # Stage 2: notes on the grid.
    midi = arrange.arrange(part, work, vocal, sr, style=style)
    midi_path = session.midi_path(name)
    midi.write(str(midi_path))

    # Stage 3: a rough audio rendering of those notes.
    guide = render_guide.render(midi, duration=work.duration, part=part)
    session.write_audio(session.guide_path(name), guide)

    # Stage 4: hand the guide to Stable Audio 3.
    prompt = prompts.build(part, work, style, instrument, production)
    log.info("generating %s [%s] seed=%d bars=%d", name, prompt, seed, len(work.bars))

    raw, backend_used, fallback_error = sa3_backend.generate_with_fallback(
        backend_id=backend,
        prompt=prompt,
        init_audio=guide,
        noise=noise,
        duration=work.duration,
        seed=seed,
    )

    # Stage 5: correct whatever drift the model introduced.
    stem = align.align(raw, guide, target_bpm=work.bpm)
    session.write_audio(session.stem_path(name), stem)

    result = StemResult(
        part=part,
        name=name,
        instrument=instrument,
        wav_path=str(session.stem_path(name).relative_to(session.root)),
        midi_path=str(midi_path.relative_to(session.root)),
        backend_used=backend_used,
        fallback_error=fallback_error,
        prompt=prompt,
        noise=noise,
        seed=seed,
        duration=float(work.duration),
        n_bars=len(work.bars),
    )
    session.save_stem(result)
    return result


# Flat spellings, for keys where a musician expects them. The parser reads
# either, but a minor-key grid showing "A#" instead of "Bb" reads as a bug.
FLAT_NAMES = {1: "Db", 3: "Eb", 6: "Gb", 8: "Ab", 10: "Bb"}


def _chord_name(pitch_class: int, mode: str) -> str:
    if mode == "minor" and pitch_class in FLAT_NAMES:
        return FLAT_NAMES[pitch_class]
    return pitch_class_to_note(pitch_class)


def create_blank_session(
    bpm: float = 100.0,
    key: str = "A",
    mode: str = "minor",
    bars: int = 8,
) -> tuple[Session, Analysis]:
    """A session with no vocal, for generating backing from nothing.

    The whole pipeline keys off an Analysis, so composing without a
    recording just means supplying one directly instead of inferring it.
    Chords come from a stock progression in the requested key, which the
    user can edit exactly like a detected one.
    """
    seconds_per_bar = (60.0 / bpm) * 4
    duration = bars * seconds_per_bar

    # A silent "vocal" keeps the session layout uniform: every other stage
    # can read vocal.wav without caring how the session started.
    session = Session.create(np.zeros(int(duration * SAMPLE_RATE), dtype=np.float32), SAMPLE_RATE)

    analysis = Analysis(
        bpm=bpm,
        downbeat_offset_s=0.0,
        key=key,
        mode=mode,
        duration=duration,
        bars=[],
    )
    # i-VI-III-VII in minor, I-V-vi-IV in major: common enough to be a
    # reasonable default and easy to recognise as "the starting point".
    #
    # Quality is listed per degree rather than derived. In natural minor
    # only the tonic is minor -- VI, III and VII are all major triads --
    # and getting that wrong makes the whole progression sound wrong.
    progression_degrees = (
        [(0, "m"), (8, ""), (3, ""), (10, "")]
        if mode == "minor"
        else [(0, ""), (7, ""), (9, "m"), (5, "")]
    )
    tonic = note_to_pitch_class(key)
    progression = [
        _chord_name((tonic + step) % 12, mode) + quality
        for step, quality in progression_degrees
    ]

    analysis.bars = [
        Bar(
            index=i,
            start=i * seconds_per_bar,
            end=(i + 1) * seconds_per_bar,
            chord=progression[i % len(progression)],
        )
        for i in range(bars)
    ]

    session.save_analysis(analysis)
    log.info("blank session %s: %.1f BPM, %s %s, %d bars", session.id, bpm, key, mode, bars)
    return session, analysis


def generate_from_reference(
    session: Session,
    reference: np.ndarray,
    prompt: str,
    noise: float = config.DEFAULT_NOISE,
    backend: str | None = None,
    seed: int | None = None,
    name: str = "clip",
) -> StemResult:
    """Generate a clip using existing audio as the guide, not a synthesized one.

    Same audio-to-audio mechanism as `generate_stem`, but the caller supplies
    the reference. That lets the studio say "make something that follows
    *this* clip" — reharmonizing a bassline over the drums, doubling a part
    on another instrument — rather than only ever following the arranger's
    idea of the chord grid.

    The reference is whatever the user picked, so it may be any length; it
    is aligned to the session grid afterwards exactly like a normal stem.
    """
    analysis = session.analysis
    seed = seed if seed is not None else random.randint(0, 2**31 - 1)
    duration = len(reference) / SAMPLE_RATE

    # Tempo and key still go in the prompt: the reference constrains rhythm
    # and harmony, but restating them reduces drift.
    full_prompt = ", ".join(
        piece
        for piece in (prompt.strip(), f"{round(analysis.bpm)} BPM", f"{analysis.key} {analysis.mode}")
        if piece
    )
    log.info("generating from reference [%s] seed=%d", full_prompt, seed)

    raw, backend_used, fallback_error = sa3_backend.generate_with_fallback(
        backend_id=backend,
        prompt=full_prompt,
        init_audio=reference,
        noise=noise,
        duration=duration,
        seed=seed,
    )

    stem = align.align(raw, reference, target_bpm=analysis.bpm)
    path = session.stem_path(name)
    session.write_audio(path, stem)

    return StemResult(
        part="bass",  # nominal; a reference clip has no arranger part
        name=name,
        instrument=prompt,
        wav_path=str(path.relative_to(session.root)),
        midi_path="",
        backend_used=backend_used,
        fallback_error=fallback_error,
        prompt=full_prompt,
        noise=noise,
        seed=seed,
        duration=len(stem) / SAMPLE_RATE,
        n_bars=max(1, round(duration / analysis.seconds_per_bar)),
    )


def generate_from_notes(
    session: Session,
    notes: list[dict],
    prompt: str,
    noise: float | None = None,
    backend: str | None = None,
    seed: int | None = None,
    name: str = "instrument",
    bars: int | None = None,
) -> StemResult:
    """Play your own notes, then have Stable Audio 3 give them a sound.

    Same guide-track mechanism as every other part, with one difference:
    the notes come from the user rather than an arranger. That makes this
    the most direct form of the whole idea — you decide what is played, the
    model decides what plays it.

    Notes are `{pitch, start, length, velocity}` with times in beats, so a
    piano roll can speak in bars and beats without knowing the tempo.
    """
    analysis = session.analysis
    seed = seed if seed is not None else random.randint(0, 2**31 - 1)
    noise = noise if noise is not None else config.default_noise("free")

    beat = analysis.seconds_per_beat
    midi = pretty_midi.PrettyMIDI(initial_tempo=analysis.bpm)
    instrument = pretty_midi.Instrument(program=0, name=name)
    midi.instruments.append(instrument)

    for note in notes:
        start = float(note["start"]) * beat
        end = start + max(float(note.get("length", 1)) * beat, 0.05)
        instrument.notes.append(
            pretty_midi.Note(
                velocity=int(note.get("velocity", 90)),
                pitch=int(np.clip(int(note["pitch"]), 0, 127)),
                start=start,
                end=end,
            )
        )

    # Long enough to hold every note, rounded up to a whole bar so the clip
    # lines up with everything else on the timeline.
    played = max((n.end for n in instrument.notes), default=analysis.seconds_per_bar)
    target_bars = bars or max(1, math.ceil(played / analysis.seconds_per_bar))
    duration = target_bars * analysis.seconds_per_bar

    midi_path = session.midi_path(name)
    midi.write(str(midi_path))

    # `free` renders a plain sustained tone: the notes are already the
    # user's, so the guide should colour them as little as possible.
    guide = render_guide.render(midi, duration=duration, part="free")
    session.write_audio(session.guide_path(name), guide)

    full_prompt = ", ".join(
        piece
        for piece in (
            prompt.strip(),
            f"{round(analysis.bpm)} BPM",
            f"{analysis.key} {analysis.mode}",
            "solo instrument, one layer only, no drums, no vocals",
        )
        if piece
    )
    log.info("generating from %d played notes [%s] seed=%d", len(notes), full_prompt, seed)

    raw, backend_used, fallback_error = sa3_backend.generate_with_fallback(
        backend_id=backend,
        prompt=full_prompt,
        init_audio=guide,
        noise=noise,
        duration=duration,
        seed=seed,
    )

    stem = align.align(raw, guide, target_bpm=analysis.bpm)
    session.write_audio(session.stem_path(name), stem)

    result = StemResult(
        part="free",
        name=name,
        instrument=prompt,
        wav_path=str(session.stem_path(name).relative_to(session.root)),
        midi_path=str(midi_path.relative_to(session.root)),
        backend_used=backend_used,
        fallback_error=fallback_error,
        prompt=full_prompt,
        noise=noise,
        seed=seed,
        duration=len(stem) / SAMPLE_RATE,
        n_bars=target_bars,
    )
    session.save_stem(result)
    return result


def mix(session: Session, parts: list[Part], include_vocal: bool = True) -> np.ndarray:
    """Sum the generated stems (and optionally the vocal) into one preview."""
    vocal, _ = session.read_vocal()
    tracks = [vocal] if include_vocal else []

    for part in parts:
        path = session.stem_path(part)
        if path.exists():
            audio, _ = sf.read(path, dtype="float32")
            tracks.append(audio)

    if not tracks:
        return np.zeros(0, dtype=np.float32)

    length = max(len(t) for t in tracks)
    total = np.zeros(length, dtype=np.float32)
    for track in tracks:
        total[: len(track)] += track

    # Headroom, so summing four stems does not clip.
    peak = float(np.max(np.abs(total)))
    return total if peak == 0 else (total / peak * 0.9).astype(np.float32)
