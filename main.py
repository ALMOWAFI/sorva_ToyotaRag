import json
import logging
import os
import re
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from retrieve import retrieve
from prompts import SYSTEM_PROMPT

load_dotenv()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "120"))
TOP_K = int(os.getenv("TOP_K", "5"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class DriverQuestion(BaseModel):
    question: str
    conversation_history: list[dict] = []  # [{role: "driver"|"assistant", text: "..."}]


class AssistantResponse(BaseModel):
    answer: str
    is_clarifying_question: bool
    sources: list[str]
    timestamp: str


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Sequoia RAG Assistant", version="1.0.0")
app.mount("/ui", StaticFiles(directory="ui"), name="ui")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(f"Unhandled error: {exc}")
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/", response_class=HTMLResponse)
async def root():
    with open("ui/index.html", encoding="utf-8") as f:
        return f.read()


@app.get("/health")
async def health():
    from pathlib import Path
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            ollama_status = "connected" if resp.status_code == 200 else "error"
    except Exception:
        ollama_status = "disconnected"

    index_path = Path(os.getenv("FAISS_INDEX_PATH", "faiss_index"))
    return {
        "status": "ok",
        "ollama": ollama_status,
        "faiss_index": "ready" if (index_path / "index.faiss").exists() else "not_built",
        "model": OLLAMA_MODEL,
    }


@app.post("/ask", response_model=AssistantResponse)
async def ask(payload: DriverQuestion) -> AssistantResponse:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Question cannot be empty.")

    logger.info(f"question={question!r}")

    # Retrieve relevant chunks from KB
    try:
        chunks = retrieve(question, top_k=TOP_K)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    context = "\n\n".join(
        f"[{c['metadata'].get('source_file', 'manual')} p.{c['metadata'].get('page', '?')}]\n{c['content'][:500]}"
        for c in chunks[:3]
    )

    # Build conversation history block
    history_block = ""
    if payload.conversation_history:
        lines = []
        for msg in payload.conversation_history[-6:]:  # last 3 exchanges
            role = "Driver" if msg["role"] == "driver" else "Assistant"
            lines.append(f"{role}: {msg['text']}")
        history_block = "\nCONVERSATION SO FAR:\n" + "\n".join(lines) + "\n"

    prompt = SYSTEM_PROMPT.format(context=context, question=question)
    if history_block:
        prompt = prompt.replace("DRIVER'S QUESTION:", history_block + "DRIVER'S QUESTION:")

    ollama_payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0.3,
            "top_p": 0.9,
            "num_predict": 300,
            "num_ctx": 2048,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
            response = await client.post(f"{OLLAMA_BASE_URL}/api/generate", json=ollama_payload)
            response.raise_for_status()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Ollama not running. Run: ollama serve")
    except httpx.ReadTimeout:
        raise HTTPException(status_code=504, detail="Model timed out.")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Ollama error: {exc.response.text[:200]}")

    answer: str = response.json().get("response", "").strip()

    # Detect if the response is a clarifying question
    is_question = answer.strip().endswith("?") or answer.lower().startswith(
        ("what", "where", "when", "which", "does", "can you", "could you", "is it", "are you")
    )

    sources = list({c["metadata"].get("source_file", "manual") for c in chunks[:3]})

    logger.info(f"answer_length={len(answer)} clarifying={is_question}")

    return AssistantResponse(
        answer=answer,
        is_clarifying_question=is_question,
        sources=sources,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=True,
    )
