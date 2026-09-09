"""Static informational site: what the bot is and a link to open it.

No accounts, no browser search/chat/calls -- all of that lives in the bot
itself. Runs as its own container (`uvicorn web.app:app`) only because that
was already the deploy shape; it does nothing beyond serving static files.
"""
import mimetypes
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent / "static"
mimetypes.add_type("image/webp", ".webp")

app = FastAPI(title="anon-chat-bot site")


@app.get("/health")
async def health():
    return {"ok": True}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
