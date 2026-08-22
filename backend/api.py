"""HTTP API and static file server.

    uv run uvicorn backend.api:app --reload

Then open http://127.0.0.1:8000

Routes are thin: they validate input, call into `pipeline`, and serialize
the result. Musical logic belongs in the pipeline modules, not here.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import soundfile as sf

from . import config, interpret, pipeline, sa3_backend
from .analysis import rebuild_bar_grid
from .models import PARTS
from .session import Session

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

config.ensure_dirs()
app = FastAPI(title="Backing Track Generator")


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
        result = pipeline.generate_stem(
            session,
            part=request.part,
            style=request.style,
            noise=request.noise,
            backend=request.backend,
            seed=request.seed,
            bars=request.bars,
            start_bar=request.start_bar,
        )
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error

    return {
        **result.to_dict(),
        # The stem file is per-part and overwritten on each call; the seed
        # query busts the browser cache so a regenerate fetches fresh audio.
        "audio_url": f"/api/session/{session.id}/audio/stems/{request.part}.wav?v={result.seed}",
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

    Uses Claude when credentials are available and falls back to keyword
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

    plan = interpret.interpret(request.text, context)
    return {**plan.model_dump(), "interpreter": "claude" if interpret.claude_available() else "rules"}


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


@app.get("/api/session/{session_id}")
def get_session(session_id: str) -> dict:
    return _load(session_id).to_dict()


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
