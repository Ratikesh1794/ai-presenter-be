from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from services.parser import parse_pptx
from services.session_store import session_store

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


@router.post("/upload")
async def upload_presentation(file: UploadFile = File(...)):
    logger.info(f"[UPLOAD] Received file: '{file.filename}' content_type='{file.content_type}'")

    # ── Validate ──────────────────────────────────────────────────────────────
    if not file.filename or not file.filename.endswith(".pptx"):
        logger.warning(f"[UPLOAD] Rejected — not a .pptx: '{file.filename}'")
        raise HTTPException(status_code=400, detail="Only .pptx files are supported.")

    raw = await file.read()
    size_kb = len(raw) / 1024
    logger.info(f"[UPLOAD] File read: {size_kb:.1f} KB")

    if len(raw) > MAX_FILE_SIZE:
        logger.warning(f"[UPLOAD] Rejected — file too large: {size_kb:.1f} KB")
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 50 MB.")

    if len(raw) == 0:
        logger.warning("[UPLOAD] Rejected — empty file")
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # ── Parse ─────────────────────────────────────────────────────────────────
    logger.info("[UPLOAD] Starting pptx parse...")
    try:
        deck = parse_pptx(raw)
    except Exception as exc:
        logger.exception(f"[UPLOAD] Parse failed: {exc}")
        raise HTTPException(status_code=422, detail=f"Failed to parse presentation: {exc}")

    logger.info(f"[UPLOAD] Parse complete — {deck.total} slides found")

    if deck.total == 0:
        logger.warning("[UPLOAD] Rejected — no slides found after parsing")
        raise HTTPException(status_code=422, detail="Presentation appears to have no slides.")

    for s in deck.slides:
        logger.debug(
            f"[UPLOAD]   Slide {s.id}: title='{s.title}' "
            f"bullets={len(s.bullets)} notes={len(s.notes)} chars"
        )

    # ── Store ─────────────────────────────────────────────────────────────────
    session_id = str(uuid.uuid4())
    session_store.save(session_id, deck)
    logger.info(f"[UPLOAD] Session stored: session_id={session_id}")

    payload = {
        "session_id": session_id,
        "slides": [
            {
                "id": s.id,
                "title": s.title,
                "subtitle": s.subtitle,
                "bullets": s.bullets,
                "notes": s.notes,
            }
            for s in deck.slides
        ],
    }

    logger.info(f"[UPLOAD] Returning {len(payload['slides'])} slides to frontend")
    return JSONResponse(payload)