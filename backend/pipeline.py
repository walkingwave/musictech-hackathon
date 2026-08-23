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
import re

import numpy as np
import pretty_midi
import soundfile as sf

from . import align, arrange, config, grooves, prompts, render_guide, sa3_backend, separate

# Imported by name: this module already has a `mix()` of its own (the export
# mixdown), and the module import was silently shadowed by it.
from .mix import cleanup_separated
from .mix import polish as polish_stem
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


# Words that mean "this part on its own". A solo take is the one case where
# the ensemble context is actively wrong: mixing the rest of the band under
# the guide is what makes parts cohere, but asked for a slap bass solo the
# model rendered the drums it could hear straight into the bass stem.
SOLO_WORDS = re.compile(
    r"\bsolo\b|\balone\b|\bunaccompanied\b|\bisolated\b|\bby itself\b|"
    r"\bon its own\b|\bjust the\b|\bnothing else\b|\ba cappella\b",
    re.IGNORECASE,
)


def _wants_solo(*texts: str) -> bool:
    return any(SOLO_WORDS.search(text or "") for text in texts)


def _gate_to_activity(
    stem: np.ndarray, analysis: Analysis, active: list[bool]
) -> np.ndarray:
    """Silence the bars this part is supposed to sit out.

    The guide already has rests there, and the model mostly follows them —
    but "mostly" is not an arrangement. Handed a silent stretch it will
    happily fill it with room tone, a held note or a tail from the bar
    before, and the space the arrangement was built around quietly
    disappears. Gating the finished audio is what guarantees it.

    Fades at the edges rather than hard cuts: a stem chopped on a sample
    boundary clicks, and a click is more audible than the note it removed.
    """
    if all(active):
        return stem

    gated = np.array(stem, dtype=np.float32, copy=True)
    fade_len = max(1, int(0.02 * SAMPLE_RATE))
    fade_in = np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
    fade_out = fade_in[::-1]

    for bar, playing in zip(analysis.bars, active):
        if playing:
            continue
        start = int(bar.start * SAMPLE_RATE)
        end = min(len(gated), int(bar.end * SAMPLE_RATE))
        if end <= start:
            continue
        # Fade the tail of the previous bar out and the head of the next one
        # in, so the silence arrives and leaves smoothly.
        head = min(fade_len, end - start)
        gated[start:start + head] *= fade_out[:head]
        gated[start + head:end] = 0.0
        tail_start = max(start, end - fade_len)
        if tail_start < end and end < len(gated):
            gated[end:end + fade_len] *= fade_in[: len(gated) - end][:fade_len]

    return gated



def _ensemble_guide(
    session: Session,
    guide: np.ndarray,
    exclude: str,
    level: float,
) -> tuple[np.ndarray, list[str]]:
    """Mix the stems already on the timeline in under the new part's guide.

    Stable Audio 3 gets one audio input, and until now that input described
    the new part alone — so every part was generated in a vacuum and had no
    way to sit with the others beyond sharing a key and a tempo. Putting the
    finished stems underneath the guide means the model hears the band it is
    joining: the room, the density, the balance, the feel.

    The guide stays dominant. It carries the notes the new part must play,
    and the bed is quiet context, not a second melody to follow — pushed too
    loud, the model starts re-rendering the whole mix instead of one part.

    Returns the combined guide and the names that went into the bed, so the
    caller can log what the part was generated against.
    """
    others = session.read_stems(
        exclude=exclude,
        limit=config.ENSEMBLE_MAX_TRACKS,
        # A finished mix IS the band; underneath a new part it would drown
        # the guide and get re-rendered wholesale.
        skip_parts=("mix",),
    )
    if not others or level <= 0:
        return guide, []

    bed = np.zeros(len(guide), dtype=np.float32)
    for _, audio in others:
        # Stems can be a hair longer or shorter than this part's guide when
        # the bar count changed mid-session; line them up at bar 1 and take
        # whatever overlaps.
        n = min(len(bed), len(audio))
        bed[:n] += audio[:n]

    peak = float(np.abs(bed).max())
    if peak < 1e-6:
        return guide, []
    bed = bed / peak

    mixed = guide + bed * level
    # Re-normalise to the guide's own peak: the model reads level as
    # intensity, and a hotter input renders as a more aggressive take.
    guide_peak = float(np.abs(guide).max()) or 1.0
    mixed_peak = float(np.abs(mixed).max()) or 1.0
    return (mixed * (guide_peak / mixed_peak)).astype(np.float32), [n for n, _ in others]



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
    # Which of several same-role tracks this is, so leads trade phrases and
    # comping parts lay out for each other instead of all playing at once.
    voice_index: int = 0,
    voice_count: int = 1,
    # None decides from the request text: a solo take gets no band under it,
    # anything else does. True or False forces it either way.
    ensemble: bool | None = None,
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
    # The seed drives the arrangement too, not just the model: regenerating
    # a part with a new seed should give a different take, not the same notes
    # with a different timbre.
    midi = arrange.arrange(
        part, work, vocal, sr, style=style, seed=seed,
        voice_index=voice_index, voice_count=voice_count,
    )
    midi_path = session.midi_path(name)
    midi.write(str(midi_path))

    # Stage 3: a rough audio rendering of those notes.
    guide = render_guide.render(midi, duration=work.duration, part=part)
    session.write_audio(session.guide_path(name), guide)

    # Stage 3b: put the band underneath it. A part generated against only its
    # own guide has no idea what it is joining; against the finished stems it
    # picks up their room, density and balance. A `mix` is the whole band
    # already, so it has nothing to join — and a solo take wants nothing
    # under it at all, or the model renders the band it can hear into the
    # stem, which is how a slap bass solo came back with drums on it.
    context_names: list[str] = []
    solo = _wants_solo(instrument, style or "")
    if ensemble is None:
        ensemble = not solo
    if part != "mix" and ensemble:
        guide, context_names = _ensemble_guide(
            session, guide, exclude=name, level=config.ENSEMBLE_LEVEL
        )
    elif solo:
        log.info("%s: solo requested, generating without ensemble context", name)

    # Stage 4: hand the guide to Stable Audio 3.
    if context_names:
        # Follow the input more closely than a bare guide would: the band is
        # in that input now, and at the default strength the model transforms
        # most of it away before it can influence anything. Kept small: this
        # dial trades cohesion against bleed, and a stem with someone else's
        # guitar in it is worse than a stem that merely sits loosely.
        noise = max(config.ENSEMBLE_MIN_STRENGTH, noise - config.ENSEMBLE_STRENGTH_DROP)
    prompt = prompts.build(part, work, style, instrument, production, bool(context_names))
    log.info(
        "generating %s [%s] seed=%d bars=%d strength=%.2f context=%s",
        name, prompt, seed, len(work.bars), noise, ",".join(context_names) or "none",
    )

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

    # Stage 6: enforce the arrangement. The guide asked this part to sit out
    # certain bars; this makes sure it actually does, whatever the model put
    # in the gaps.
    stem = _gate_to_activity(
        stem, work, arrange.activity(part, len(work.bars), voice_index, voice_count)
    )

    # Stage 7: sit it in the mix. Carve the low end it does not own and level
    # it to its role, so five stems arriving at "as loud as possible" do not
    # fight — most of what reads as clashing is low-frequency masking plus a
    # level war, and both are fixable deterministically.
    stem = polish_stem(stem, part)
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

# Live status of long generate_song runs, keyed by session id. The whole
# pipeline (master, two separations, per-stem work) can take minutes on CPU,
# and a request that quiet looks hung — the UI polls this instead.
PROGRESS: dict[str, str] = {}


def _progress(session_id: str, message: str) -> None:
    if message:
        PROGRESS[session_id] = message
    else:
        PROGRESS.pop(session_id, None)



def generate_song(
    session: Session,
    tracks: list[dict],
    style: str | None = None,
    production: str | None = None,
    backend: str | None = None,
    seed: int | None = None,
    bars: int | None = None,
) -> list[StemResult]:
    """Master-first generation: one record, then carve the stems out of it.

    Generating each part as its own model call — however much shared text,
    seed and context they get — produces N separate performances that merely
    agree on a key, and stacking them sounds exactly like that. This flips
    the order: Stable Audio 3 renders the WHOLE band once (the thing it is
    actually best at, since ensembles are most of its training data), and
    then each stem is an audio-to-audio pass over that master asking for one
    instrument, at low strength so the timing, harmony and room of the master
    survive. Every stem is the same performance by construction — one track
    split into stems, rather than stems hoping to add up to a track.
    """
    analysis = session.analysis
    vocal, sr = session.read_vocal()

    style, bars, production, tone_seed = _resolve_arrangement(
        session, style, bars, analysis, production, seed
    )
    seed = seed if seed is not None else tone_seed
    work = _extend_analysis(analysis, bars, 0)

    # The master's guide is built from the REQUESTED parts — each track
    # arranged with its own voice position, rendered, and summed. Two things
    # depend on this being exact: an instrument absent from the guide cannot
    # be carved out of the master (a quintet's guitar used to be missing
    # entirely, so its "stem" was whatever the mask guessed), and the
    # score-split templates must describe the SAME notes the master plays —
    # they are these very guides.
    template_guides: list[np.ndarray] = []
    for t in tracks:
        t_midi = arrange.arrange(
            t["part"], work, vocal, sr, style=style, seed=seed,
            voice_index=int(t.get("voice_index", 0)),
            voice_count=int(t.get("voice_count", 1)),
        )
        template_guides.append(
            render_guide.render(t_midi, duration=work.duration, part=t["part"])
        )
    master_guide = np.sum(template_guides, axis=0).astype(np.float32)
    peak = float(np.abs(master_guide).max())
    if peak > 0:
        master_guide = master_guide * (0.8 / peak)
    band = ", ".join(t.get("instrument") or t.get("name") or t["part"] for t in tracks)
    master_prompt = prompts.build("mix", work, style, band, production)
    log.info("master [%s] seed=%d bars=%d", master_prompt, seed, len(work.bars))

    _progress(session.id, "Recording the master take…")
    master_raw, master_backend, master_error = sa3_backend.generate_with_fallback(
        backend_id=backend,
        prompt=master_prompt,
        init_audio=master_guide,
        noise=config.MASTER_STRENGTH,
        duration=work.duration,
        seed=seed,
    )
    master = align.align(master_raw, master_guide, target_bpm=work.bpm)
    # Kept on disk for debugging and for regenerating single stems later.
    session.write_audio(session.guide_path("_master"), master)

    results: list[StemResult] = []

    # Split the master with a real source separator when one is installed.
    # The generative carve ("only the piano from this recording") re-imagines
    # the part instead of isolating it, so the stems came back mislabeled and
    # only loosely related to the master; Demucs returns the master's own
    # audio per instrument, so the stems are correct by definition.
    separated: dict[str, np.ndarray] | None = None
    timbre_evidence: dict[str, np.ndarray] = {}
    if separate.available():
        _progress(session.id, "Splitting the master into stems…")
        want_evidence = any(
            separate.source_for(t["part"], t.get("instrument") or t.get("name") or "")
            in ("piano", "guitar")
            for t in tracks
        )
        try:
            separated, timbre_evidence = separate.separate(master, want_evidence)
        except Exception as error:  # noqa: BLE001 - fall back to the carve
            log.warning("separation failed, falling back to carve: %s", error)

    # One pass over all requests, so shared sources can be split score-aware.
    # The templates are the same guides the master was generated from, so
    # they describe exactly the notes the master plays.
    allocated: list[tuple[np.ndarray, str]] | None = None
    if separated is not None:
        allocated = separate.allocate(
            separated,
            [(t["part"], (t.get("instrument") or t.get("name") or "")) for t in tracks],
            master,
            templates=template_guides,
            evidence=timbre_evidence,
        )

    for index, track in enumerate(tracks):
        part = track["part"]
        name = track.get("name") or part
        _progress(session.id, f"Cutting {name}…")
        instrument = (track.get("instrument") or "").strip()
        voice_index = int(track.get("voice_index", 0))
        voice_count = int(track.get("voice_count", 1))

        if allocated is not None:
            stem, source = allocated[index]
            backend_used, fallback_error = master_backend, master_error
            stem_prompt = f"{master_prompt} [demucs:{source}]"
            # Deliberately untouched: both an SA3 refinement pass and DSP
            # cleanup (spectral gate + expander) were tried here and both
            # audibly hurt more than the separation residue they removed.
            # The stem is the master's own audio, played as-is.
            log.info("split stem %s <- demucs %s", name, source)
        else:
            described = instrument or prompts.INSTRUMENT_PHRASES.get(part, part)
            stem_prompt = ", ".join(
                piece
                for piece in (
                    f"only the {described} from this exact recording",
                    "the identical performance with every other instrument removed",
                    f"{round(work.bpm)} BPM",
                    f"{work.key} {work.mode}",
                    (production or "").strip(),
                    prompts.ISOLATION.get(part, ""),
                )
                if piece
            )
            log.info("split stem %s [%s]", name, stem_prompt)
            raw, backend_used, fallback_error = sa3_backend.generate_with_fallback(
                backend_id=backend,
                prompt=stem_prompt,
                init_audio=master,
                noise=config.SPLIT_STRENGTH,
                duration=work.duration,
                seed=seed,
            )
            stem = align.align(raw, master, target_bpm=work.bpm)

        if allocated is None:
            # Only the carve path is gated and polished: a separated stem IS
            # the master's own performance — its arrangement, balance and
            # spectrum are already right, and re-balancing or filtering it
            # breaks the property that makes it good, which is that the
            # stems sum back to the master. Play them all and you hear the
            # master; mute one and exactly that part goes away.
            stem = _gate_to_activity(
                stem, work, arrange.activity(part, len(work.bars), voice_index, voice_count)
            )
            stem = polish_stem(stem, part)
        session.write_audio(session.stem_path(name), stem)

        # The MIDI export still comes from the arranger, so the user gets an
        # editable approximation even though the audio came from the master.
        part_midi = arrange.arrange(
            part, work, vocal, sr, style=style, seed=seed,
            voice_index=voice_index, voice_count=voice_count,
        )
        midi_path = session.midi_path(name)
        part_midi.write(str(midi_path))

        result = StemResult(
            part=part,
            name=name,
            instrument=instrument,
            wav_path=str(session.stem_path(name).relative_to(session.root)),
            midi_path=str(midi_path.relative_to(session.root)),
            backend_used=backend_used,
            fallback_error=fallback_error or master_error,
            prompt=stem_prompt,
            noise=config.SPLIT_STRENGTH,
            seed=seed,
            duration=len(stem) / SAMPLE_RATE,
            n_bars=len(work.bars),
        )
        session.save_stem(result)
        results.append(result)

    _progress(session.id, "")
    return results
