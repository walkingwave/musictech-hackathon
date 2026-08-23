"""Split a finished master into per-instrument stems with Demucs.

The master-first pipeline needs "this exact recording, minus the other
instruments" — and a generative model cannot do that: asked for "only the
piano from this recording" it re-imagines a piano rather than isolating the
one that is there, so the stems came back mislabeled and only loosely
related to the master. Source separation is the tool that actually does the
job. Demucs' six-source model splits a mix into drums, bass, guitar, piano,
vocals and other, and the result is the master's own audio — the timing,
harmony and room agree between stems because they are literally the same
recording.

CPU-only here: a 20-second master separates in well under a minute, and the
model weights (~300MB) download once on first use.
"""

from __future__ import annotations

import logging

import numpy as np

from .config import SAMPLE_RATE

log = logging.getLogger(__name__)

# The fine-tuned four-stem model: a bag of four Demucs, noticeably higher
# SDR than the six-source variant on drums, bass and vocals — the only
# sources this pipeline still trusts on their own. Its missing piano and
# guitar buckets cost nothing: all harmonic content is pooled and divided
# by score, which beats the 6s model's two weakest sources anyway.
MODEL_NAME = "htdemucs_ft"

# Demucs source name for each of our parts. `melody`, `harmony` and `free`
# have no fixed instrument, so the caller refines them with _source_for using
# the instrument description; these are the fallbacks.
PART_SOURCE: dict[str, str] = {
    "drums": "drums",
    "bass": "bass",
    "guitar": "guitar",
    "piano": "piano",
    "melody": "other",
    "harmony": "other",
    "free": "other",
}

# Instrument words that pin a track to a specific separated source, whatever
# its part says. Order matters: "synth bass" must hit bass before anything
# else. Bare "synth" is deliberately absent — Demucs has no synth source, a
# synth lives in "other", and mapping it to piano handed four EDM tracks the
# near-silent piano bucket and they all came out empty.
_KEYWORD_SOURCE = (
    (("drum", "percussion", "kit", "beat"), "drums"),
    (("bass", "808", "sub"), "bass"),
    (("guitar", "ukulele", "banjo", "mandolin"), "guitar"),
    (("piano", "keys", "rhodes", "wurlitzer", "organ"), "piano"),
    (("vocal", "voice", "choir", "aah"), "vocals"),
)

# A mapped source counts as present only if it holds a real share of the
# master's energy. Demucs returns faint residue rather than digital silence,
# so an absolute threshold never fires.
ENERGY_FLOOR = 0.05

# When several tracks end up sharing one source (three synth parts all in
# "other"), each takes the band its role lives in, so they are not three
# copies of the same audio. (low_hz, high_hz); None means unbounded.
PART_BAND: dict[str, tuple[float | None, float | None]] = {
    "bass": (None, 250.0),
    "drums": (None, None),
    "piano": (180.0, 5000.0),
    "guitar": (180.0, 5000.0),
    "harmony": (250.0, 8000.0),
    "melody": (400.0, None),
    "free": (200.0, None),
}



def available() -> bool:
    try:
        import demucs.apply  # noqa: F401
        import demucs.pretrained  # noqa: F401
    except ImportError:
        return False
    return True


# The witness model. Its piano and guitar buckets are too dirty to PLAY, but
# as evidence of where piano-ness and guitar-ness live in the spectrogram
# they are exactly the timbre knowledge the score templates lack.
WITNESS_MODEL = "htdemucs_6s"

# How strongly the witness's timbre evidence counts next to the score in the
# pool split. Score stays primary: it is never wrong about timing.
EVIDENCE_WEIGHT = 0.7

# Exponent on the final pool masks. 1 is plain proportional sharing; higher
# pushes contested bins toward the leading track — less bleed, at the cost
# of slightly harder edges.
MASK_SHARPNESS = 1.6

_models: dict = {}


def _load(name: str = MODEL_NAME):
    if name not in _models:
        from demucs.pretrained import get_model

        log.info("loading %s (first use downloads the weights)", name)
        model = get_model(name)
        model.eval()
        _models[name] = model
    return _models[name]


def _run_model(name: str, master: np.ndarray) -> dict[str, np.ndarray]:
    import torch
    from demucs.apply import apply_model

    model = _load(name)
    # Demucs wants (batch, channels, samples) stereo at its own sample rate;
    # ours is mono at 44.1kHz, which htdemucs also uses.
    mono = torch.from_numpy(np.asarray(master, dtype=np.float32))
    wav = mono.unsqueeze(0).repeat(2, 1).unsqueeze(0)  # (1, 2, n)

    with torch.no_grad():
        out = apply_model(
            model, wav, device="cpu", progress=False, shifts=0, overlap=0.25
        )[0]  # (sources, 2, n)

    stems: dict[str, np.ndarray] = {}
    for i, source in enumerate(model.sources):
        stems[source] = out[i].mean(dim=0).cpu().numpy().astype(np.float32)
    log.info("%s separated master into: %s", name, ", ".join(model.sources))
    return stems


def separate(
    master: np.ndarray, want_evidence: bool = True
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Mono master -> (playable stems, timbre evidence).

    Two models, two jobs. The fine-tuned four-stem model provides the audio:
    its drums, bass and vocals are the strongest separations available, and
    they are re-masked so the stems sum to the master exactly. The six-source
    model provides the EVIDENCE: its piano and guitar buckets are too smeared
    to play, but they mark where piano-ness and guitar-ness sit in the
    spectrogram, and the score-split uses them to divide the harmonic pool.
    """
    master = np.asarray(master, dtype=np.float32)
    stems = _run_model(MODEL_NAME, master)

    # The witness's piano and guitar buckets are used as PLAYABLE sources.
    # An earlier iteration used them only as mask evidence and divided the
    # harmonic pool by each part's synthetic score template — but the
    # templates are saws at a fixed octave, and masking a saxophone by an
    # octave-5 saw stripped its fundamental and left a chipmunked husk.
    # Real separated audio, jointly remasked, keeps the timbre honest.
    if want_evidence:
        try:
            witness = _run_model(WITNESS_MODEL, master)
            for name in ("piano", "guitar"):
                if name in witness:
                    stems[name] = witness[name]
        except Exception as error:  # noqa: BLE001 - the witness is optional
            log.warning("witness model failed, continuing without it: %s", error)

    return remask(stems, master), {}


def source_for(part: str, instrument: str = "") -> str:
    """Which separated source a requested track should be cut from."""
    lowered = (instrument or "").lower()
    for words, source in _KEYWORD_SOURCE:
        if any(w in lowered for w in words):
            return source
    return PART_SOURCE.get(part, "other")


# Roughly where a part's register sits, used only to ORDER tracks sharing a
# source before the spectrum is divided between them.
_PART_REGISTER = {"bass": 0, "drums": 1, "free": 2, "guitar": 3, "piano": 3, "harmony": 4, "melody": 5}

# Crossover points when 2, 3 or 4 tracks share one source, in Hz.
_CROSSOVERS = {2: (500.0,), 3: (300.0, 2000.0), 4: (250.0, 1000.0, 4000.0)}


def _crossover_split(audio: np.ndarray, cuts: tuple[float, ...]) -> list[np.ndarray]:
    """Divide a signal into len(cuts)+1 COMPLEMENTARY bands.

    Complementary is the point: each split is lowpass/highpass at the same
    frequency, so the pieces sum back to (nearly) the original. Independent
    band-passes with overlapping edges combed when the tracks were played
    together, which read as "Demucs degraded the audio" when it was the
    slicing doing it.
    """
    from scipy.signal import butter, sosfilt

    bands: list[np.ndarray] = []
    remainder = np.asarray(audio, dtype=np.float32)
    for cut in cuts:
        low = sosfilt(butter(2, cut, btype="lowpass", fs=SAMPLE_RATE, output="sos"), remainder)
        bands.append(np.asarray(low, dtype=np.float32))
        remainder = (remainder - low).astype(np.float32)  # exact complement
    bands.append(remainder)
    return bands


# Demucs sources worth trusting on their own. Its drums, bass and vocals
# separations are strong; `piano` and `guitar` are its two documented weak
# sources and routinely swap content — a quintet's piano stem arrived
# carrying the guitar. Everything harmonic is pooled and divided by score
# instead, where the templates know exactly whose note is whose.
TRUSTED_SOURCES = ("drums", "bass", "vocals", "piano", "guitar")
HARMONIC_POOL = ("other",)


def allocate(
    separated: dict[str, np.ndarray],
    requests: list[tuple[str, str]],
    master: np.ndarray,
    templates: list[np.ndarray | None] | None = None,
    evidence: dict[str, np.ndarray] | None = None,
) -> list[tuple[np.ndarray, str]]:
    """Give each requested (part, instrument) its slice of the separation.

    Two regimes:
    - a request whose instrument maps to a TRUSTED source (drums, bass,
      vocals) takes that bucket, gated by an energy floor because Demucs
      returns residue rather than silence for an absent instrument;
    - every other request shares the pooled harmonic audio (piano + guitar
      + other summed), divided by each track's own score template — any
      instrument, any register, and no dependence on Demucs' weakest
      buckets. Complementary crossovers remain the no-template fallback.
    """
    master_rms = float(np.sqrt(np.mean(np.square(master)))) or 1.0

    def usable(name: str) -> bool:
        stem = separated.get(name)
        if stem is None:
            return False
        return float(np.sqrt(np.mean(np.square(stem)))) >= ENERGY_FLOOR * master_rms

    out: list[tuple[np.ndarray, str] | None] = [None] * len(requests)
    pool_indexes: list[int] = []
    for i, (part, instrument) in enumerate(requests):
        source = source_for(part, instrument)
        if source in TRUSTED_SOURCES and usable(source):
            out[i] = (np.array(separated[source], dtype=np.float32, copy=True), source)
        else:
            pool_indexes.append(i)

    if not pool_indexes:
        return [x for x in out if x is not None]

    pool = np.zeros_like(master)
    for name in HARMONIC_POOL:
        audio = separated.get(name)
        if audio is not None:
            n = min(len(pool), len(audio))
            pool[:n] += np.asarray(audio[:n], dtype=np.float32)

    if len(pool_indexes) == 1:
        i = pool_indexes[0]
        out[i] = (pool, f"pool:{requests[i][0]}")
        return [x for x in out if x is not None]

    # Complementary crossovers only. Score-template masking was tried here
    # and reverted: the templates are fixed-octave saws, and masking a real
    # instrument by one ripped out its fundamental whenever the model played
    # in a different register.
    ordered = sorted(pool_indexes, key=lambda i: _PART_REGISTER.get(requests[i][0], 3))
    cuts = _CROSSOVERS.get(len(ordered), _CROSSOVERS[4])
    bands = _crossover_split(pool, cuts[: len(ordered) - 1])
    for band, i in zip(bands, ordered):
        out[i] = (band, f"pool:{requests[i][0]}-band")
    return [x for x in out if x is not None]


# Sharpness of the re-masking. 1 keeps Demucs' own softness, 2 is standard
# Wiener filtering, higher pushes each bin harder toward its dominant stem
# (less bleed, more risk of musical noise at the edges).
REMASK_ALPHA = 2.0


def remask(separated: dict[str, np.ndarray], master: np.ndarray) -> dict[str, np.ndarray]:
    """Redistribute the master's spectrogram by stem dominance, per bin.

    Demucs decides softly, and its indecision is audible as bleed: a bin
    that is 70% drums and 30% piano ends up quietly in both stems. This
    pass re-cuts every time-frequency bin of the MASTER between the stems
    in proportion to |stem|^alpha — with alpha 2, that 70/30 bin becomes
    ~84/16, and the piano's copy of the drum hit drops below notice.

    Two properties fall out for free:
    - the stems sum to the master EXACTLY (the masks sum to one), so
      playing all tracks is bit-for-bit the record;
    - every sample in a stem is the master's own audio, never invented.
    """
    from scipy.signal import istft, stft

    nperseg, noverlap = 4096, 3072
    _, _, master_spec = stft(master, fs=SAMPLE_RATE, nperseg=nperseg, noverlap=noverlap)

    powers: dict[str, np.ndarray] = {}
    for name, stem in separated.items():
        aligned = np.zeros_like(master)
        n = min(len(master), len(stem))
        aligned[:n] = stem[:n]
        _, _, spec = stft(aligned, fs=SAMPLE_RATE, nperseg=nperseg, noverlap=noverlap)
        powers[name] = np.abs(spec) ** REMASK_ALPHA

    total = sum(powers.values()) + 1e-12

    out: dict[str, np.ndarray] = {}
    for name, power in powers.items():
        _, audio = istft(master_spec * (power / total), fs=SAMPLE_RATE, nperseg=nperseg, noverlap=noverlap)
        audio = np.asarray(audio, dtype=np.float32)
        if len(audio) < len(master):
            audio = np.pad(audio, (0, len(master) - len(audio)))
        out[name] = audio[: len(master)]
    return out


def informed_split(
    source: np.ndarray,
    templates: list[np.ndarray],
    evidences: list[np.ndarray | None] | None = None,
) -> list[np.ndarray]:
    """Divide one separated source between tracks using scores and evidence.

    The score template says WHICH NOTES a part played — precise in time and
    fundamental, ignorant of timbre. The evidence (a witness separator's
    piano or guitar bucket) says WHAT THE INSTRUMENT SOUNDS LIKE — smeared
    in time, right about the harmonics. Each is weak alone and they fail
    differently, so the mask combines them: per track, normalised template
    power plus normalised evidence power, then everything renormalised so
    the slices still sum back to the source.
    """
    from scipy.signal import istft, stft

    nperseg, noverlap = 4096, 3072
    _, _, source_spec = stft(source, fs=SAMPLE_RATE, nperseg=nperseg, noverlap=noverlap)

    def power_of(audio: np.ndarray, alpha: float) -> np.ndarray:
        aligned = np.zeros_like(source)
        n = min(len(source), len(audio))
        aligned[:n] = audio[:n]
        _, _, spec = stft(aligned, fs=SAMPLE_RATE, nperseg=nperseg, noverlap=noverlap)
        p = np.abs(spec) ** alpha
        return p / (p.mean() + 1e-12)  # unit mean, so sources are comparable

    powers = []
    for i, template in enumerate(templates):
        # Alpha below remask's 2: guide timbres are saw/sine approximations,
        # and over-trusting their exact harmonic mix punishes the real
        # instrument for not being a sawtooth.
        p = power_of(template, 1.5)
        evidence = evidences[i] if evidences else None
        if evidence is not None and float(np.abs(evidence).max()) > 1e-5:
            p = p + EVIDENCE_WEIGHT * power_of(evidence, 2.0)
        powers.append(p)

    # Sharpen the assignment: raising each mask to a power >1 (and
    # renormalising) pushes contested bins toward whichever track already
    # leads, which is where audible bleed lives. Kept moderate — hard
    # winner-take-all masks ring.
    masks = [(p / (sum(powers) + 1e-12)) ** MASK_SHARPNESS for p in powers]
    total = sum(masks) + 1e-12

    out: list[np.ndarray] = []
    for mask in masks:
        _, audio = istft(source_spec * (mask / total), fs=SAMPLE_RATE, nperseg=nperseg, noverlap=noverlap)
        audio = np.asarray(audio, dtype=np.float32)
        if len(audio) < len(source):
            audio = np.pad(audio, (0, len(source) - len(audio)))
        out.append(audio[: len(source)])
    return out
