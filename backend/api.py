"""HTTP API and static file server.

    uv run uvicorn backend.api:app --reload

Then open http://127.0.0.1:8000

Routes are thin: they validate input, call into `pipeline`, and serialize
the result. Musical logic belongs in the pipeline modules, not here.
"""

from __future__ import annotations

import io
import logging
import threading
import zipfile
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import soundfile as sf

from . import compose, config, instruments, interpret, pipeline, sa3_backend
from .analysis import rebuild_bar_grid
from .models import PARTS
from .session import Session

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

config.ensure_dirs()
app = FastAPI(title="Backing Track Generator")

_active_sessions: set[str] = set()
_deleting_sessions: set[str] = set()
_active_sessions_lock = threading.Lock()


@contextmanager
def _generation_for(session_id: str):
    with _active_sessions_lock:
        if session_id in _active_sessions or session_id in _deleting_sessions:
            raise HTTPException(409, "generation is already active or the session is closing")
        _active_sessions.add(session_id)
    try:
        yield
    finally:
        with _active_sessions_lock:
            _active_sessions.discard(session_id)


# --- request bodies -----------------------------------------------------


class GenerateRequest(BaseModel):
    session_id: str
    part: str
    style: str | None = None  # None -> inherit the session's arrangement
    noise: float | None = None  # None -> per-part default
    backend: str | None = None
    seed: int | None = None
    bars: int | None = None  # target length in bars; None -> input vocal length
    start_bar: int = 0  # chord-grid offset, for section regeneration
    name: str | None = None  # track name; several tracks may share a part
    instrument: str = ""  # replaces the part's default instrument description
    # Shared recording character for the whole arrangement. None inherits
    # whatever the session already agreed on.
    production: str | None = None


class AnalysisEdit(BaseModel):
    """User corrections to the detected structure. Every field optional.

    Detection is good but not perfect, and a wrong key or tempo poisons
    every stem generated afterwards. Correcting it has to be one edit, not
    a re-record.
    """

    bpm: float | None = None
    key: str | None = None
    mode: str | None = None
    chords: list[str] | None = None  # one per bar, in order


# --- routes -------------------------------------------------------------


@app.get("/api/backends")
def list_backends() -> list[dict]:
    """Which backends exist and which can run right now.

    The UI uses `available` to disable options rather than letting the
    user pick something that will fail.
    """
    return sa3_backend.describe()


@app.post("/api/analyze")
async def analyze(file: UploadFile) -> dict:
    """Upload a vocal, get back a session id and the detected structure."""
    upload_path = config.SESSIONS_DIR / f"upload-{file.filename}"
    upload_path.write_bytes(await file.read())

    try:
        session, analysis = pipeline.analyze_vocal(upload_path)
        session.set_display_name(Path(file.filename or "Recording").stem)
    finally:
        upload_path.unlink(missing_ok=True)

    return {"session_id": session.id, "analysis": analysis.to_dict()}


@app.patch("/api/session/{session_id}/analysis")
def update_analysis(session_id: str, edit: AnalysisEdit) -> dict:
    """Apply user corrections to the detected structure.

    Later generations use the corrected values. Changing the tempo
    rebuilds the bar grid, since bar boundaries are derived from it.
    """
    session = _load(session_id)
    analysis = session.analysis

    if edit.key is not None:
        analysis.key = edit.key
    if edit.mode is not None:
        if edit.mode not in ("major", "minor"):
            raise HTTPException(400, "mode must be 'major' or 'minor'")
        analysis.mode = edit.mode

    if edit.bpm is not None:
        if not 20 <= edit.bpm <= 300:
            raise HTTPException(400, "bpm must be between 20 and 300")
        analysis.bpm = edit.bpm
        rebuild_bar_grid(analysis)

    if edit.chords is not None:
        if len(edit.chords) != len(analysis.bars):
            raise HTTPException(
                400, f"expected {len(analysis.bars)} chords, got {len(edit.chords)}"
            )
        for bar, chord in zip(analysis.bars, edit.chords):
            bar.chord = chord

    session.save_analysis(analysis)
    return analysis.to_dict()


@app.post("/api/generate")
def generate(request: GenerateRequest) -> dict:
    """Generate one backing stem.

    `backend_used` in the response may differ from what was requested, if
    the chosen backend failed and the pipeline fell back.
    """
    if request.part not in PARTS:
        raise HTTPException(400, f"unknown part: {request.part}")

    session = _load(request.session_id)
    try:
        with _generation_for(session.id):
            result = pipeline.generate_stem(
                session,
                part=request.part,
                style=request.style,
                noise=request.noise,
                backend=request.backend,
                seed=request.seed,
                bars=request.bars,
                start_bar=request.start_bar,
                name=pipeline.track_name(session, request.part, request.name),
                instrument=request.instrument,
                production=request.production,
            )
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error

    return {
        **result.to_dict(),
        # Files are keyed by track name, not part, so two pitched tracks do
        # not overwrite each other. The seed query busts the browser cache
        # so a regenerate fetches fresh audio.
        "audio_url": f"/api/session/{session.id}/audio/stems/{result.name}.wav?v={result.seed}",
    }


class InterpretRequest(BaseModel):
    text: str
    session_id: str | None = None


@app.post("/api/interpret")
def interpret_request(request: InterpretRequest) -> dict:
    """Turn a plain-English request into a generation plan.

    When a session is given, its style, tempo, key and existing parts are
    passed as context so a follow-up ("add a piano") extends the current
    arrangement instead of starting a conflicting one.

    Uses DeepSeek when credentials are available and falls back to keyword
    matching otherwise, so this endpoint always returns a usable plan
    rather than failing when offline.
    """
    context = None
    if request.session_id:
        try:
            session = Session.load(request.session_id)
            analysis = session.analysis
            arrangement = session.arrangement
            context = interpret.Context(
                style=arrangement.style,
                bars=arrangement.bars,
                bpm=analysis.bpm,
                key=analysis.key,
                mode=analysis.mode,
                existing_parts=list(session.to_dict().get("stems", {})),
            )
        except (FileNotFoundError, ValueError):
            context = None  # unanalyzed or missing session - no context to add

    plan, source = interpret.interpret_with_source(request.text, context)
    return {**plan.model_dump(), "interpreter": source}


class ComposeMidiRequest(BaseModel):
    text: str
    session_id: str | None = None
    # The client may already know the length it wants (a clip being replaced,
    # or the arrangement's bar count); otherwise the composer picks.
    bars: int | None = None
    bpm: float | None = None
    key: str | None = None
    mode: str | None = None
    style: str | None = None


@app.post("/api/compose-midi")
def compose_midi(request: ComposeMidiRequest) -> dict:
    """Write a MIDI phrase from a description.

    Returns notes in beats, not audio: the client puts them on a MIDI track
    and plays them through the sampler, so the result stays editable in the
    piano roll instead of being baked into a wav.

    Session settings seed the context, and anything passed explicitly on the
    request wins over them — the Studio's tempo box is more current than the
    tempo detected from the original vocal.
    """
    context = compose.Context(
        bpm=request.bpm,
        key=request.key,
        mode=request.mode,
        bars=request.bars,
        style=request.style or "",
    )
    if request.session_id:
        try:
            session = Session.load(request.session_id)
            analysis = session.analysis
            arrangement = session.arrangement
            context = compose.Context(
                bpm=request.bpm or analysis.bpm,
                key=request.key or analysis.key,
                mode=request.mode or analysis.mode,
                bars=request.bars or arrangement.bars,
                style=request.style or arrangement.style or "",
            )
        except (FileNotFoundError, ValueError):
            pass  # unanalyzed or missing session - the request's own values stand

    phrase, source = compose.compose(request.text, context)
    if not phrase.notes:
        raise HTTPException(422, "the composer returned no notes - try describing the part differently")
    return {**phrase.model_dump(), "composer": source}


class SamplesRequest(BaseModel):
    prompt: str
    takes: int | None = None
    backend: str | None = None
    seed: int | None = None
    force: bool = False


@app.post("/api/instrument/samples")
def instrument_samples(request: SamplesRequest) -> dict:
    """Generate one sustained one-shot per pitch for an instrument.

    The browser then plays MIDI through these, so the notes are exactly
    what was played and editing needs no further generation. Results are
    cached by prompt, so loading the same instrument again is free.
    """
    if not request.prompt.strip():
        raise HTTPException(400, "an instrument needs a description")

    try:
        made = instruments.generate_samples(
            request.prompt,
            takes=max(1, min(6, request.takes or instruments.DEFAULT_TAKES)),
            backend=request.backend,
            seed=request.seed,
            force=request.force,
        )
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error

    ident = instruments.instrument_id(request.prompt)
    return {
        "instrument_id": ident,
        # Takes are not asked to hit a pitch; each one lands where it
        # lands and reports it, and the sampler transposes from there.
        "samples": [
            {
                "actual_pitch": s["actual_pitch"],
                "url": f"/api/instrument/{ident}/{s['index']}.wav",
            }
            for s in made
        ],
    }


@app.get("/api/instrument/{ident}/{index}.wav")
def instrument_sample(ident: str, index: int) -> FileResponse:
    path = config.CACHE_DIR / "instruments" / ident / f"{index}.wav"
    if not path.is_file():
        raise HTTPException(404, "sample not found")
    return FileResponse(path, media_type="audio/wav")


class MidiNote(BaseModel):
    pitch: int
    start: float  # beats from the clip's start
    length: float = 1.0  # beats
    velocity: int = 90


class MidiRequest(BaseModel):
    session_id: str | None = None
    notes: list[MidiNote]
    prompt: str = ""
    noise: float | None = None
    backend: str | None = None
    seed: int | None = None
    name: str = "instrument"
    bars: int | None = None
    # Only used when no session exists yet, so the instrument view works
    # before anything else has been recorded or generated.
    bpm: float = 100.0
    key: str = "A"
    mode: str = "minor"


@app.post("/api/generate-from-midi")
def generate_from_midi(request: MidiRequest) -> dict:
    """Turn played notes into a real instrument.

    The notes become the guide track, so the performance is preserved
    exactly while Stable Audio 3 supplies the sound described by `prompt`.
    """
    if not request.notes:
        raise HTTPException(400, "no notes to generate from")

    if request.session_id:
        session = _load(request.session_id)
    else:
        session, _ = pipeline.create_blank_session(
            bpm=request.bpm, key=request.key, mode=request.mode, bars=request.bars or 8
        )

    try:
        with _generation_for(session.id):
            result = pipeline.generate_from_notes(
                session,
                notes=[n.model_dump() for n in request.notes],
                prompt=request.prompt,
                noise=request.noise,
                backend=request.backend,
                seed=request.seed,
                name=pipeline.track_name(session, "free", request.name),
                bars=request.bars,
            )
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error

    return {
        **result.to_dict(),
        "session_id": session.id,
        "audio_url": f"/api/session/{session.id}/audio/stems/{result.name}.wav?v={result.seed}",
    }


@app.get("/api/session/{session_id}/arrangement")
def get_arrangement(session_id: str) -> dict:
    """The style and length every part in this session shares."""
    return _load(session_id).arrangement.to_dict()


class BlankSessionRequest(BaseModel):
    bpm: float = 100.0
    key: str = "A"
    mode: str = "minor"
    bars: int = 8


@app.post("/api/session/blank")
def create_blank_session(request: BlankSessionRequest) -> dict:
    """Start a session with no vocal, for composing from nothing.

    Everything downstream keys off an Analysis, so writing to a project
    without a recording just means supplying tempo, key and a starting
    chord grid instead of inferring them.
    """
    if not 20 <= request.bpm <= 300:
        raise HTTPException(400, "bpm must be between 20 and 300")
    if request.mode not in ("major", "minor"):
        raise HTTPException(400, "mode must be 'major' or 'minor'")
    if not 1 <= request.bars <= 128:
        raise HTTPException(400, "bars must be between 1 and 128")

    session, analysis = pipeline.create_blank_session(
        bpm=request.bpm, key=request.key, mode=request.mode, bars=request.bars
    )
    return {"session_id": session.id, "analysis": analysis.to_dict()}


@app.post("/api/generate-from-reference")
async def generate_from_reference(
    session_id: str = Form(...),
    prompt: str = Form(""),
    noise: float = Form(config.DEFAULT_NOISE),
    backend: str | None = Form(None),
    seed: int | None = Form(None),
    name: str = Form("clip"),
    audio: UploadFile = File(...),
) -> dict:
    """Generate a clip guided by audio the user supplies, not a synthesized guide.

    The studio posts the rendered audio of whichever track was chosen as the
    reference. Same audio-to-audio mechanism as a normal stem — the reference
    simply takes the place of the arranger's guide track.
    """
    session = _load(session_id)

    reference, sr = sf.read(io.BytesIO(await audio.read()), dtype="float32")
    if reference.ndim > 1:
        reference = reference.mean(axis=1)
    if sr != config.SAMPLE_RATE:
        import librosa

        reference = librosa.resample(reference, orig_sr=sr, target_sr=config.SAMPLE_RATE)

    try:
        with _generation_for(session.id):
            result = pipeline.generate_from_reference(
                session,
                reference=reference,
                prompt=prompt,
                noise=noise,
                backend=backend,
                seed=seed,
                name=_safe_name(name),
            )
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error

    return {
        **result.to_dict(),
        "audio_url": f"/api/session/{session.id}/audio/stems/{_safe_name(name)}.wav",
    }


def _safe_name(name: str) -> str:
    """Filesystem-safe stem name. These become paths, so no traversal."""
    cleaned = "".join(c for c in name if c.isalnum() or c in "-_") or "clip"
    return cleaned[:40]


@app.get("/api/sessions")
def list_sessions() -> list[dict]:
    """List bounded, safe summaries of persisted local sessions."""
    summaries = []
    for root in (config.SESSIONS_DIR.iterdir() if config.SESSIONS_DIR.exists() else ()):
        if not root.is_dir() or root.is_symlink():
            continue
        try:
            summaries.append(Session.load(root.name).summary())
        except (FileNotFoundError, ValueError, OSError):
            continue
    return sorted(summaries, key=lambda item: item["updated_at"] or "", reverse=True)[:100]


@app.get("/api/session/{session_id}")
def get_session(session_id: str) -> dict:
    return _load(session_id).to_dict()


@app.delete("/api/session/{session_id}")
def delete_session(session_id: str) -> dict:
    session = _load(session_id)
    with _active_sessions_lock:
        if session.id in _active_sessions or session.id in _deleting_sessions:
            raise HTTPException(409, "wait for generation to finish before deleting this session")
        _deleting_sessions.add(session.id)
    try:
        session.delete()
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    finally:
        with _active_sessions_lock:
            _deleting_sessions.discard(session.id)
    return {"deleted": session.id}


@app.get("/api/session/{session_id}/audio/{kind}/{filename}")
def get_audio(session_id: str, kind: str, filename: str) -> FileResponse:
    """Serve a wav from a session. `kind` is guides or stems."""
    if kind not in ("guides", "stems"):
        raise HTTPException(404, "not found")

    session = _load(session_id)
    path = session.root / kind / filename
    if not path.is_file():
        raise HTTPException(404, "not found")

    return FileResponse(path, media_type="audio/wav")


@app.get("/api/session/{session_id}/vocal.wav")
def get_vocal(session_id: str) -> FileResponse:
    return FileResponse(_load(session_id).vocal_path, media_type="audio/wav")


@app.get("/api/session/{session_id}/export")
def export(session_id: str) -> StreamingResponse:
    """Zip of every stem, its MIDI, the vocal, and the provenance manifest.

    This is the deliverable a musician actually takes away: drop the WAVs
    on separate DAW tracks, or open the MIDI to re-voice the parts.
    """
    session = _load(session_id)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(session.vocal_path, "vocal.wav")
        archive.write(session.meta_path, "meta.json")
        for subdir in ("stems", "midi"):
            for path in sorted((session.root / subdir).glob("*")):
                archive.write(path, f"{subdir}/{path.name}")

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="backing-{session_id}.zip"'},
    )


def _load(session_id: str) -> Session:
    try:
        return Session.load(session_id)
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error


# Mounted last, so it does not shadow the /api routes above.
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
