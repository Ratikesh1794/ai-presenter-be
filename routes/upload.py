from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from services.parser import parse_pptx
from services.session_store import session_store
from services.slide_renderer import convert_pptx_to_images

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


@router.post("/upload")
async def upload_presentation(file: UploadFile = File(...)):
    logger.info(f"[UPLOAD] Received: '{file.filename}'")

    if not file.filename or not file.filename.endswith(".pptx"):
        raise HTTPException(status_code=400, detail="Only .pptx files are supported.")

    raw = await file.read()
    logger.info(f"[UPLOAD] File size: {len(raw) / 1024:.1f} KB")

    if len(raw) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large. Maximum 50 MB.")
    if len(raw) == 0:
        raise HTTPException(status_code=400, detail="File is empty.")

    # ── Parse text content for agent ──────────────────────────────────────────
    logger.info("[UPLOAD] Parsing slide text...")
    try:
        deck = parse_pptx(raw)
    except Exception as exc:
        logger.exception(f"[UPLOAD] Parse failed: {exc}")
        raise HTTPException(status_code=422, detail=f"Failed to parse presentation: {exc}")

    if deck.total == 0:
        raise HTTPException(status_code=422, detail="Presentation has no slides.")

    logger.info(f"[UPLOAD] Parsed {deck.total} slides")

    # ── Convert slides to images ───────────────────────────────────────────────
    session_id = str(uuid.uuid4())
    logger.info(f"[UPLOAD] Converting slides to images, session={session_id}...")

    try:
        image_urls = await convert_pptx_to_images(
            pptx_bytes=raw,
            session_id=session_id,
            output_dir=session_store.slides_dir,
        )
    except RuntimeError as exc:
        logger.error(f"[UPLOAD] Image conversion failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    logger.info(f"[UPLOAD] Generated {len(image_urls)} slide images")

    # ── Store session ─────────────────────────────────────────────────────────
    session_store.save(session_id, deck, image_urls)

    return JSONResponse({
        "session_id": session_id,
        "slides": [
            {
                "id": s.id,
                "title": s.title,
                "subtitle": s.subtitle,
                "bullets": s.bullets,
                "notes": s.notes,
                # Actual image URL for this slide
                "image_url": image_urls[s.id] if s.id < len(image_urls) else None,
            }
            for s in deck.slides
        ],
    })