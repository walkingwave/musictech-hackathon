"""One-shot samples for an instrument, so MIDI can be played back exactly.

Rendering a whole bar through audio-to-audio asks the model to reproduce a
melody, and it does not: measured against a played C major scale it
returned C G C A C A D. A sawtooth guide holds the notes far better than a
sine, but only because the output then keeps the guide's spectrum — which
means it is polishing the guide rather than inventing an instrument.

So do not ask it to play the part at all. Generate a handful of sustained
one-shots across the range, and let a sampler play the MIDI. The notes are
then exactly what was played, editing needs no regeneration, and the model
only has to do the thing it is good at: make one note sound like something.

Samples are cached on disk by prompt, so loading the same instrument onto
another track costs nothing.
"""

from __future__ import annotations

import hashlib
import logging

import numpy as np
import pretty_midi
import soundfile as sf

from . import config, render_guide, sa3_backend

log = logging.getLogger(__name__)

# How many takes to sample per instrument. Each is a separate generation,
# so this is the main cost. Three gives the sampler a few pitches to pick
# between without making a library load feel like a wait.
DEFAULT_TAKES = 3

# Appended to every instrument description. Without it the model plays a
# short musical phrase rather than one note — pitch wandering a whole
# octave across the take — and a sampler built on that warbles on every
# note. "Chord" and "ensemble" are the specific enemies: neither has a
# single pitch to sample.
SAMPLE_SUFFIX = (
    "playing one single sustained note with its natural attack and decay, "
    "solo instrument alone, monophonic, dry close mic, no reverb, "
    "no other instruments, no drums, no melody"
)

# Generate longer than the sample needs, then keep the steadiest stretch.
TAKE_SECONDS = 6.0
SEGMENT_SECONDS = 2.4

# High on purpose. The guide is a sawtooth, and at low divergence the model
# returns that sawtooth with a polish rather than an instrument: measured
# against flute, cello and piano prompts at 0.6, every output correlated
# 0.96-0.98 with the guide's spectrum and the three differed from each
# other by 0.02 — indistinguishable. At 0.85 they differ by 0.81.
#
# The cost is pitch: at 0.85 the model drifts, sometimes by an octave. That
# is affordable only because each sample's true pitch is measured after the
# fact and the sampler transposes from it (see detect_pitch). Do not lower
# this to fix a tuning problem — it trades the instrument away for it.
SAMPLE_NOISE = 0.85

# The sampler transposes from whatever pitch a sample actually landed on,
# so drift is survivable — but a large stretch at playback audibly slows or
# speeds the tone. Retry to land close, and keep the closest attempt.
# A fifth. The sampler transposes from wherever a sample landed, so drift
# is not fatal — only a large stretch at playback is. Tightening this to a
# major third tripled generation time to 38s an instrument, because nearly
# every pitch retried; the tolerance costs far less than the wait.
MAX_DRIFT = 7
RETRIES = 1


def instrument_id(prompt: str) -> str:
    """Stable id for a prompt, so identical instruments share their cache."""
    return hashlib.sha256(prompt.strip().lower().encode()).hexdigest()[:16]


def sample_dir(prompt: str):
    return config.CACHE_DIR / "instruments" / instrument_id(prompt)


def sample_path(prompt: str, pitch: int):
    return sample_dir(prompt) / f"{pitch}.wav"


def steadiest_segment(audio: np.ndarray, want: float) -> np.ndarray:
    """The most pitch-stable stretch of a generated take.

    Asked for one sustained note, the model tends to play a short phrase
    instead — pitch wandering several semitones across the take. Sampling
    that whole thing gives an instrument that warbles on every note. So
    generate longer than needed and keep only the steadiest window, which
    is the part that actually behaves like a held note.
    """
    import librosa

    hop = 512
    f0, voiced, _ = librosa.pyin(
        audio,
        fmin=float(librosa.note_to_hz("C2")),
        fmax=float(librosa.note_to_hz("C7")),
        sr=config.SAMPLE_RATE,
        hop_length=hop,
    )
    midi = librosa.hz_to_midi(f0)
    frames_wanted = max(4, int(want * config.SAMPLE_RATE / hop))
    if len(midi) <= frames_wanted:
        return audio

    # Loudness per frame, so a decaying instrument does not win on
    # steadiness by being nearly silent. A plucked guitar is the case that
    # forced this: its quietest tail is also its steadiest stretch, and
    # picking it produced samples a fraction of the level of every other
    # instrument in the library.
    energy = librosa.feature.rms(y=audio, hop_length=hop, frame_length=2048)[0]
    # 95th percentile, not max: one transient spike in a take made every
    # window look quiet relative to it, every window failed the level
    # filter, and the "sliced" sample was silently the whole six seconds.
    loudest = float(np.percentile(energy, 95)) or 1.0

    best_start, best_score = 0, -np.inf
    for start in range(0, len(midi) - frames_wanted, 2):
        window = midi[start : start + frames_wanted]
        sung = window[np.isfinite(window)]
        # Require the window to be mostly voiced, or a silent stretch wins
        # by having no pitch to vary.
        if len(sung) < frames_wanted * 0.7:
            continue

        level = float(np.mean(energy[start : start + frames_wanted])) / loudest
        if level < 0.25:
            continue  # too quiet to be the body of the note

        spread = float(np.percentile(sung, 90) - np.percentile(sung, 10))
        # Steadiness matters most, but not to the point of choosing a
        # whisper over a note.
        score = -spread + level
        if score > best_score:
            best_start, best_score = start, score

    if not np.isfinite(best_score):
        # Every window failed the filters. Falling back to the raw take put
        # a six-second phrase in the sampler; the loudest window is always a
        # better sample than that.
        centers = np.array([
            float(np.mean(energy[s : s + frames_wanted]))
            for s in range(0, len(midi) - frames_wanted, 2)
        ])
        best_start = int(np.argmax(centers)) * 2
        return audio[best_start * hop : (best_start + frames_wanted) * hop]

    # Walk back from the steady window to the note's onset: the nearest
    # earlier frame where energy rises sharply from below. A sample without
    # its attack is unrecognisable — the steady middle of a guitar, a piano
    # and an organ all sound like the organ. Cap the walk so a long crescendo
    # cannot drag the window to the take's very start.
    onset_start = best_start
    max_walk = int(1.0 * config.SAMPLE_RATE / hop)
    for i in range(best_start, max(0, best_start - max_walk) - 1, -1):
        if i == 0:
            onset_start = 0
            break
        if energy[i - 1] < 0.15 * loudest <= energy[i]:
            onset_start = i - 1
            break

    return audio[onset_start * hop : (onset_start + frames_wanted) * hop]



def _first_note_only(audio: np.ndarray) -> np.ndarray:
    """Cut a take at its second attack, keeping exactly one note.

    Asked for a single note, the model sometimes plays two — a re-strum, a
    grace note into the real one. A sample containing two onsets plays two
    onsets on every key the sampler puts it on, so the double-strum has to
    die here, not in playback. Onsets are energy rises out of a quiet
    stretch; everything from the second one onwards is dropped, with a fade
    so the cut does not click.
    """
    hop = 512
    window = 2048
    frames = max(1, (len(audio) - window) // hop)
    energy = np.array([
        float(np.sqrt(np.mean(np.square(audio[i * hop : i * hop + window]))))
        for i in range(frames)
    ])
    if not energy.size:
        return audio
    peak = float(energy.max()) or 1.0

    onsets = []
    armed = True  # ready to fire on the first rise
    for i, level in enumerate(energy):
        if armed and level >= 0.3 * peak:
            onsets.append(i)
            armed = False
        elif not armed and level < 0.12 * peak:
            armed = True  # fell back to quiet: the next rise is a new note

    # Keep the first note; cut just before the second onset. Guard against
    # cutting a sample too short to be usable.
    if len(onsets) >= 2 and onsets[1] * hop > int(0.35 * config.SAMPLE_RATE):
        cut = onsets[1] * hop
        fade = min(int(0.03 * config.SAMPLE_RATE), cut)
        out = np.array(audio[:cut], dtype=np.float32, copy=True)
        out[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
        return out
    return audio


def _one_note_guide(pitch: int, seconds: float) -> np.ndarray:
    """A single sustained note, rendered with harmonics.

    Sawtooth rather than sine on purpose: measured across a noise sweep, a
    saw guide kept 100% of played pitches up to 0.6 divergence where a sine
    guide fell to 0%. A sine gives the model almost nothing to lock onto.
    """
    midi = pretty_midi.PrettyMIDI(initial_tempo=120)
    instrument = pretty_midi.Instrument(program=0, name="sample")
    instrument.notes.append(
        pretty_midi.Note(velocity=100, pitch=pitch, start=0.0, end=seconds)
    )
    midi.instruments.append(instrument)

    previous = render_guide.WAVEFORMS.get("free")
    render_guide.WAVEFORMS["free"] = "saw"
    try:
        return render_guide.render(midi, duration=seconds, part="free")
    finally:
        render_guide.WAVEFORMS["free"] = previous


def generate_samples(
    prompt: str,
    takes: int = DEFAULT_TAKES,
    backend: str | None = None,
    seed: int | None = None,
    force: bool = False,
) -> list[dict]:
    """Sample an instrument: a few steady single notes of it.

    Generated from the prompt alone, with no guide track. An earlier
    version conditioned on a sawtooth to pin the pitch, and every
    instrument inherited the sawtooth — flute, cello and piano came back
    correlating 0.96-0.98 with the guide's spectrum and differing from each
    other by 0.02. The pitch it bought was not worth the instrument it cost.

    Nothing is asked to land on a particular pitch, because nothing needs
    to: each take's pitch is measured afterwards and the sampler transposes
    from it. That is also what makes dropping the guide affordable.
    """
    directory = sample_dir(prompt)
    directory.mkdir(parents=True, exist_ok=True)

    out = []
    for index in range(takes):
        path = directory / f"{index}.wav"

        if force or not path.exists():
            # Seeds vary per take: one seed would give near-identical
            # samples that sound cloned rather than like one instrument.
            audio, backend_used, _fallback_error = sa3_backend.generate_with_fallback(
                backend_id=backend,
                prompt=f"{prompt}, {SAMPLE_SUFFIX}",
                init_audio=None,
                noise=0.0,
                duration=TAKE_SECONDS,
                seed=(seed + index) if seed is not None else None,
            )
            audio = _trim_and_fade(_first_note_only(steadiest_segment(audio, SEGMENT_SECONDS)))
            sf.write(path, audio, config.SAMPLE_RATE)
            log.info("sampled %s take %d via %s", instrument_id(prompt), index, backend_used)

        pitch = detect_pitch(sf.read(path, dtype="float32")[0])
        # A take with no readable pitch is unusable as a sampler source;
        # dropping it beats letting the sampler transpose from a guess.
        if pitch is None:
            log.info("take %d of %s had no readable pitch; dropped", index, instrument_id(prompt))
            continue
        out.append({"index": index, "actual_pitch": pitch, "path": str(path)})

    if not out:
        # Every take was unusable. Saying so beats returning an empty set,
        # which loads "successfully" and then plays nothing.
        raise RuntimeError(
            "could not sample that instrument - try describing a single "
            "sustained note, e.g. 'bowed cello, warm and woody'"
        )

    return out


def detect_pitch(audio: np.ndarray) -> int | None:
    """The sample's actual fundamental, as a MIDI note number.

    Stored alongside each sample so the sampler transposes from the pitch
    that was *generated* rather than the one that was asked for. The model
    still occasionally jumps an octave, and measuring beats hoping.

    Uses the strongest spectral peak below 1.5kHz rather than a pitch
    tracker: on a bright sawtooth-derived tone, trackers routinely lock to
    the first harmonic and report an octave high.
    """
    import librosa

    window = audio[: config.SAMPLE_RATE * 2]
    if len(window) < 2048:
        return None

    spectrum = np.abs(np.fft.rfft(window))
    freqs = np.fft.rfftfreq(len(window), 1 / config.SAMPLE_RATE)
    band = (freqs > 40) & (freqs < 1500)
    if not band.any():
        return None

    peak = freqs[band][int(np.argmax(spectrum[band]))]
    return int(round(float(librosa.hz_to_midi(peak))))


def _retune(audio: np.ndarray, semitones: float, seconds: float) -> np.ndarray:
    """Resample a note onto a different pitch.

    Kept for callers that genuinely want a fixed pitch, but NOT used when
    generating instrument samples: shifting a sample onto its intended
    pitch moves its whole spectrum, which undoes the timbre that the high
    divergence was there to produce. See generate_samples.
    """
    if abs(semitones) < 0.5:
        return audio

    ratio = 2 ** (semitones / 12)
    source = np.arange(len(audio), dtype=np.float64)
    target = np.arange(0, len(audio), ratio, dtype=np.float64)
    shifted = np.interp(target, source, audio).astype(np.float32)

    want = int(seconds * config.SAMPLE_RATE)
    if len(shifted) >= want:
        return shifted[:want]
    # Shifting down leaves it short; loop the sustain to fill rather than
    # padding with silence, which would end the note early.
    tail = shifted[len(shifted) // 4 :]
    while len(shifted) < want and len(tail):
        shifted = np.concatenate([shifted, tail])
    return shifted[:want]


def _trim_and_fade(audio: np.ndarray, threshold: float = 0.02) -> np.ndarray:
    """Drop leading silence and fade the tail.

    Leading silence would delay every note the sampler triggers, turning a
    tight part into a sloppy one. The fade stops the sampler clicking when
    it cuts a sample short for a brief note.
    """
    loud = np.flatnonzero(np.abs(audio) > threshold)
    if len(loud):
        audio = audio[loud[0] :]

    fade = min(int(0.02 * config.SAMPLE_RATE), len(audio) // 4)
    if fade > 0:
        audio = audio.copy()
        audio[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)

    # Level every sample the same. Instruments come back at wildly
    # different loudnesses - a plucked guitar an eighth the level of a
    # sustained trumpet - and without this the quiet ones are inaudible
    # under the rest of the mix however good the sample is.
    peak = float(np.max(np.abs(audio)))
    if peak > 1e-4:
        audio = (audio / peak * 0.9).astype(np.float32)
    return audio
