from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def _find_soffice() -> str:
    candidates = [
        "soffice",
        "libreoffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/usr/bin/soffice",
        "/usr/lib/libreoffice/program/soffice",
    ]
    for c in candidates:
        if shutil.which(c) or Path(c).exists():
            return c
    raise RuntimeError(
        "LibreOffice not found. Install with: brew install --cask libreoffice"
    )


async def _run(cmd: list[str], timeout: int = 120) -> tuple[str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return stdout.decode(), stderr.decode()


async def convert_pptx_to_images(
    pptx_bytes: bytes,
    session_id: str,
    output_dir: Path,
) -> list[str]:
    """
    Convert .pptx → one PNG per slide.
    Strategy: pptx → pdf (preserves slide pages) → pdf pages → png
    Returns list of relative URL paths: ["/slides/{session_id}/0.png", ...]
    """
    session_dir = output_dir / session_id

    # Return cached images if already converted
    existing = sorted(session_dir.glob("*.png"), key=lambda p: int(p.stem))
    if existing:
        logger.info(f"[RENDERER] Cache hit for {session_id}: {len(existing)} images")
        return [f"/slides/{session_id}/{p.name}" for p in existing]

    session_dir.mkdir(parents=True, exist_ok=True)
    soffice = _find_soffice()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pptx_file = tmp_path / "deck.pptx"
        pptx_file.write_bytes(pptx_bytes)

        # ── Step 1: pptx → pdf (one page per slide, accurate layout) ─────────
        logger.info("[RENDERER] Step 1: converting pptx → pdf...")
        stdout, stderr = await _run([
            soffice,
            "--headless", "--norestore", "--nofirststartwizard",
            "--convert-to", "pdf",
            "--outdir", str(tmp_path),
            str(pptx_file),
        ])
        logger.debug(f"[RENDERER] pdf stdout: {stdout[:200]}")

        pdf_file = tmp_path / "deck.pdf"
        if not pdf_file.exists():
            raise RuntimeError(f"PDF conversion failed. stderr: {stderr[:300]}")

        logger.info(f"[RENDERER] PDF created: {pdf_file.stat().st_size / 1024:.1f} KB")

        # ── Step 2: pdf pages → png using pdftoppm (poppler) ─────────────────
        pdftoppm = shutil.which("pdftoppm")
        if pdftoppm:
            logger.info("[RENDERER] Step 2: pdf → png via pdftoppm")
            stdout, stderr = await _run([
                pdftoppm,
                "-png", "-r", "150",      # 150 DPI — good quality, reasonable size
                str(pdf_file),
                str(tmp_path / "slide"),  # output prefix → slide-1.png, slide-2.png...
            ])
            logger.debug(f"[RENDERER] pdftoppm stdout: {stdout[:200]}")

            raw_pngs = sorted(
                tmp_path.glob("slide-*.png"),
                key=lambda p: int(p.stem.split("-")[-1])
            )
        else:
            # Fallback: use LibreOffice to convert pdf → png
            logger.info("[RENDERER] pdftoppm not found, falling back to soffice pdf→png")
            stdout, stderr = await _run([
                soffice,
                "--headless", "--norestore",
                "--convert-to", "png",
                "--outdir", str(tmp_path),
                str(pdf_file),
            ])
            raw_pngs = sorted(
                tmp_path.glob("deck*.png"),
                key=lambda p: p.stem
            )

        if not raw_pngs:
            raise RuntimeError(
                f"No PNG files generated. pdftoppm stderr: {stderr[:300]}"
            )

        logger.info(f"[RENDERER] Raw PNGs: {[p.name for p in raw_pngs]}")

        # ── Move to session dir with clean 0-indexed names ────────────────────
        urls: list[str] = []
        for i, src in enumerate(raw_pngs):
            dest = session_dir / f"{i}.png"
            shutil.copy(src, dest)
            urls.append(f"/slides/{session_id}/{i}.png")

    logger.info(f"[RENDERER] Done: {len(urls)} slide images for session {session_id}")
    return urls