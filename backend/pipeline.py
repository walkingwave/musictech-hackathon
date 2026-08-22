"""The pipeline: vocal in, backing stems out.

This module is the one place that knows the order of operations. Every
other backend module does exactly one stage and knows nothing about the
others, so this is where to look to understand the flow.

    analyze_vocal()  stage 1
    generate_stem()  stages 2-5, for one part
"""

from __future__ import annotations

import logging
import random

import numpy as np
import soundfile as sf

from . import align, arrange, config, prompts, render_guide, sa3_backend
from .analysis import analyze
from .config import SAMPLE_RATE
from .models import Analysis, Bar, Part, StemResult
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


def generate_stem(
    session: Session,
    part: Part,
    style: str = "",
    noise: float | None = None,
    backend: str | None = None,
    seed: int | None = None,
    bars: int | None = None,
    start_bar: int = 0,
) -> StemResult:
    """Stages 2-5 for one part: arrange, render a guide, generate, align.

    The guide track is what makes the output land in time and in key. It
    carries the rhythm and harmony through the model's noised latent, so
    what comes back has the right skeleton and a real instrument's timbre.

    `noise` defaults per part - see config.PART_NOISE.
    """
    analysis = session.analysis
    vocal, sr = session.read_vocal()
    seed = seed if seed is not None else random.randint(0, 2**31 - 1)
    noise = noise if noise is not None else config.default_noise(part)

    # A longer target length (or a section offset) works over a tiled copy of
    # the chord grid; the original vocal analysis stays untouched on disk.
    work = analysis if bars is None else _extend_analysis(analysis, bars, start_bar)

    # Stage 2: notes on the grid.
    midi = arrange.arrange(part, work, vocal, sr, style=style)
    midi_path = session.midi_path(part)
    midi.write(str(midi_path))

    # Stage 3: a rough audio rendering of those notes.
    guide = render_guide.render(midi, duration=work.duration, part=part)
    session.write_audio(session.guide_path(part), guide)

    # Stage 4: hand the guide to Stable Audio 3.
    prompt = prompts.build(part, work, style)
    log.info("generating %s [%s] seed=%d bars=%d", part, prompt, seed, len(work.bars))

    raw, backend_used = sa3_backend.generate_with_fallback(
        backend_id=backend,
        prompt=prompt,
        init_audio=guide,
        noise=noise,
        duration=work.duration,
        seed=seed,
    )

    # Stage 5: correct whatever drift the model introduced.
    stem = align.align(raw, guide, target_bpm=work.bpm)
    session.write_audio(session.stem_path(part), stem)

    result = StemResult(
        part=part,
        wav_path=str(session.stem_path(part).relative_to(session.root)),
        midi_path=str(midi_path.relative_to(session.root)),
        backend_used=backend_used,
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

    raw, backend_used = sa3_backend.generate_with_fallback(
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
        part=name,
        wav_path=str(path.relative_to(session.root)),
        midi_path="",
        backend_used=backend_used,
        prompt=full_prompt,
        noise=noise,
        seed=seed,
        duration=len(stem) / SAMPLE_RATE,
        n_bars=max(1, round(duration / analysis.seconds_per_bar)),
    )


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
