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

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, pipeline, sa3_backend
from .models import PARTS, Analysis
from .session import Session

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

config.ensure_dirs()
app = FastAPI(title="Backing Track Generator")


# --- request bodies -----------------------------------------------------


class GenerateRequest(BaseModel):
    session_id: str
    part: str
    style: str = ""
    noise: float = config.DEFAULT_NOISE
    backend: str | None = None
    seed: int | None = None


class ChordsRequest(BaseModel):
    chords: list[str]  # one per bar, in order


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


@app.patch("/api/session/{session_id}/chords")
def update_chords(session_id: str, request: ChordsRequest) -> dict:
    """Replace the detected chords with the user's corrections.

    Chord detection on a solo vocal is genuinely ambiguous, so the user
    gets the final say. Later generations use the corrected grid.
    """
    session = _load(session_id)
    analysis = session.analysis

    if len(request.chords) != len(analysis.bars):
        raise HTTPException(400, f"expected {len(analysis.bars)} chords, got {len(request.chords)}")

    for bar, chord in zip(analysis.bars, request.chords):
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
        )
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error

    return {
        **result.to_dict(),
        "audio_url": f"/api/session/{session.id}/audio/stems/{request.part}.wav",
    }


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
