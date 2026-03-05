import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

load_dotenv()

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("watchfiles").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("python_multipart").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

if not os.environ.get("LLM_API_KEY"):
    raise RuntimeError("LLM_API_KEY environment variable is not set.")

logger.info("Environment validated — LLM_API_KEY present")

from routes.upload import router as upload_router
from routes.websocket import router as ws_router

app = FastAPI(title="AI Voice Presenter", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Serve generated slide images at /slides/{session_id}/{n}.png ──────────────
SLIDES_DIR = Path(__file__).parent / "static" / "slides"
SLIDES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/slides", StaticFiles(directory=str(SLIDES_DIR)), name="slides")

app.include_router(upload_router)
app.include_router(ws_router)


@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/health")
def health():
    return {"status": "healthy"}