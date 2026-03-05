# PresenterAI Backend

The backend is the control center of PresenterAI. It ingests presentations, extracts structure from slides, renders visual assets, runs the AI presenter loop, and streams real-time instructions to the client over WebSocket.

## What This Service Does

- Accepts `.pptx` uploads.
- Parses each slide into a normalized deck model (`title`, `subtitle`, `bullets`, `notes`).
- Converts slides into PNG images for frontend rendering.
- Orchestrates an AI presentation lifecycle:
  - introduction
  - slide-by-slide narration
  - interruption handling for user doubts
  - automatic resume and completion signaling
- Tracks token usage and estimated API cost per run.

## Basic System Design

### Core Components

- `main.py`: FastAPI bootstrap, CORS configuration, static mount for generated slide images.
- `routes/upload.py`: upload API, validation, parsing, rendering trigger, session creation.
- `routes/websocket.py`: bidirectional real-time protocol and presentation state orchestration.
- `services/agent.py`: prompt construction, tool-calling logic, and LLM response interpretation.
- `services/parser.py`: PowerPoint text extraction and normalization.
- `services/slide_renderer.py`: conversion pipeline (`pptx -> pdf -> png`).
- `services/session_store.py`: in-memory session registry.
- `services/conetion_manager.py`: per-connection runtime state machine.
- `services/cost_tracker.py`: API usage accounting.

### Request and Runtime Workflow

1. User uploads a `.pptx` to `POST /upload`.
2. Backend validates file type/size, parses deck content, and renders slide images.
3. Backend returns `session_id` and slide metadata.
4. Frontend opens WebSocket at `/ws`, sends `load_deck`, then `start_presentation`.
5. Backend starts the presenter loop, emits:
   - `change_slide`
   - `speak`
   - `status`
   - `presentation_complete`
6. If user speaks mid-presentation, backend interrupts current task, answers the doubt, and resumes from the saved slide.

## Prerequisites

### 1) Python

- Python 3.10+ recommended

### 2) System Tools (Required)

Install these on your machine before running the backend:

```bash
brew install --cask libreoffice   # pptx -> pdf
brew install poppler              # pdf -> png per page (pdftoppm)
```

`libreoffice` is mandatory for conversion. `poppler` provides `pdftoppm`, which is preferred for page-to-image rendering quality and consistency.

### 3) OpenAI-Compatible API Key

Set `LLM_API_KEY` in your environment.

## Environment Configuration

Create `backend/.env` from the example:

```bash
cp .env.example .env
```

Expected variables:

```env
LLM_API_KEY="your-api-key-here"
CORS_ORIGINS=http://localhost:5173
```

## Installation

From the `backend` directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run the Server

From the `backend` directory:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Service endpoints:
- API root: `http://localhost:8000/`
- Health check: `http://localhost:8000/health`
- Upload endpoint: `http://localhost:8000/upload`
- WebSocket endpoint: `ws://localhost:8000/ws`
- Slide assets: `http://localhost:8000/slides/<session_id>/<index>.png`

## API Surface (Quick Reference)

### HTTP

- `POST /upload`
  - Input: multipart file (`.pptx`)
  - Output: session ID + parsed slides + image URLs

### WebSocket Messages

Client -> Server:
- `load_deck`
- `start_presentation`
- `user_speech`
- `interrupt`
- `slide_changed`

Server -> Client:
- `status`
- `change_slide`
- `speak`
- `interrupted`
- `cost_info`
- `presentation_complete`
- `error`

## Operational Notes

- Sessions are in-memory; restarting the backend clears uploaded session context.
- Generated slide images are written under `backend/static/slides/<session_id>/`.
- Ensure frontend `VITE_API_URL` and `VITE_WS_URL` point to this backend instance.
