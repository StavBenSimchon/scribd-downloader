"""Scribd downloader web app. One backend, one queue, one Chrome at a time."""

import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

DOWNLOADS = Path(os.getenv("SCRIBD_OUTPUT_DIR", "/downloads")).resolve()
SCRIPT = Path(os.getenv("SCRIBD_SCRIPT", "/app/scribd-downloader.py"))
JOB_TIMEOUT_MINUTES = int(os.getenv("SCRIBD_JOB_TIMEOUT_MINUTES", "30"))
INDEX = Path(__file__).resolve().parent / "index.html"

app = FastAPI(title="Scribd Downloader")

jobs: list[dict] = []  # oldest first
lock = threading.Lock()

PAGE_RE = re.compile(r"Page (\d+)/(\d+)")
SAVED_RE = re.compile(r"PDF saved successfully to:\s*(.+)")


class NewJob(BaseModel):
    url: str


def public_view(job: dict) -> dict:
    """Job as the frontend sees it (no internals)."""
    return {
        "id": job["id"],
        "url": job["url"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "file": job["file"],
        "error": job["error"],
        "created": job["created"],
    }


def run_job(job: dict) -> None:
    job["status"] = "working"
    job["message"] = "starting chrome..."
    job["progress"] = 1
    saved_path = None
    tail: list[str] = []

    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT), job["url"], "--format", "pdf", "-o", str(DOWNLOADS)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )

    # watchdog: never let one job eat the queue forever
    timer = threading.Timer(JOB_TIMEOUT_MINUTES * 60, proc.kill)
    timer.start()
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            tail.append(line)
            tail = tail[-8:]
            job["message"] = line[:200]

            m = PAGE_RE.search(line)
            if m:
                done, total = int(m.group(1)), int(m.group(2))
                if total > 0:
                    job["progress"] = min(99, max(1, round(done / total * 100)))

            m = SAVED_RE.search(line)
            if m:
                saved_path = Path(m.group(1).strip())
        proc.wait()
    finally:
        timer.cancel()

    if proc.returncode == 0 and saved_path is not None and saved_path.is_file():
        job["status"] = "done"
        job["progress"] = 100
        job["file"] = saved_path.name
        job["message"] = "done"
    else:
        job["status"] = "error"
        job["error"] = tail[-1] if tail else f"process exited with code {proc.returncode}"
        job["message"] = job["error"]


def worker_loop() -> None:
    while True:
        job = None
        with lock:
            for j in jobs:
                if j["status"] == "waiting":
                    job = j
                    break
        if job is None:
            time.sleep(1)
            continue
        try:
            run_job(job)
        except Exception as exc:  # never kill the worker
            job["status"] = "error"
            job["error"] = str(exc)[:200]


threading.Thread(target=worker_loop, daemon=True).start()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(INDEX)


@app.post("/api/jobs")
def create_job(body: NewJob) -> dict:
    url = body.url.strip()
    if "scribd.com" not in url.lower():
        raise HTTPException(status_code=400, detail="Not a Scribd link")
    job = {
        "id": uuid.uuid4().hex[:8],
        "url": url,
        "status": "waiting",
        "progress": 0,
        "message": "waiting in queue",
        "file": None,
        "error": None,
        "created": time.time(),
    }
    with lock:
        jobs.append(job)
        job["position"] = sum(1 for j in jobs if j["status"] == "waiting")
    return public_view(job)


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    with lock:
        return [public_view(j) for j in reversed(jobs)]


@app.get("/api/files")
def list_files() -> list[dict]:
    out = []
    for p in DOWNLOADS.glob("*.pdf"):
        st = p.stat()
        out.append({"name": p.name, "size": st.st_size, "modified": st.st_mtime})
    out.sort(key=lambda f: f["modified"], reverse=True)
    return out


@app.get("/api/files/{name}")
def download_file(name: str) -> FileResponse:
    path = (DOWNLOADS / name).resolve()
    if path.parent != DOWNLOADS or not path.is_file() or path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=path.name, media_type="application/pdf")
