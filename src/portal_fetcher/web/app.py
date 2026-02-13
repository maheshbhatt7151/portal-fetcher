"""FastAPI web application for Portal Fetcher."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from portal_fetcher.adapters import ADAPTER_REGISTRY

app = FastAPI(title="Portal Fetcher")

# ── Static files ──────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent / "static"
OUTPUT_DIR = Path("./output").resolve()

# ── In-memory job store ───────────────────────────────────────
jobs: dict[str, dict[str, Any]] = {}


class FetchRequest(BaseModel):
    portal: str
    portal_url: str
    login_user: str
    login_pass: str
    subscriber: str
    headless: bool = True
    timeout: int = 30000


# ── Routes ────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main HTML page."""
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text())


@app.get("/api/portals")
async def list_portals():
    """Return available portal adapter names."""
    from portal_fetcher.selector_store import list_configs

    all_names = set(ADAPTER_REGISTRY.keys()) | set(list_configs())
    return {"portals": sorted(all_names)}


@app.post("/api/fetch")
async def start_fetch(req: FetchRequest):
    """Start a fetch job and return its ID immediately."""
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {
        "status": "running",
        "progress": [],
        "result": None,
    }

    asyncio.create_task(_run_job(job_id, req))
    return {"job_id": job_id}


@app.get("/api/fetch/{job_id}")
async def get_job(job_id: str):
    """Poll a job's status."""
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    job = jobs[job_id]
    return {
        "status": job["status"],
        "progress": job["progress"],
        "result": job["result"],
    }


@app.get("/api/fetch/{job_id}/stream")
async def stream_job(job_id: str):
    """SSE stream of job progress and final result."""
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")

    async def event_stream():
        seen = 0
        while True:
            job = jobs[job_id]
            # Send new progress messages
            progress = job["progress"]
            while seen < len(progress):
                yield f"data: {json.dumps({'type': 'progress', 'message': progress[seen]})}\n\n"
                seen += 1
            # Check if done
            if job["status"] in ("completed", "failed"):
                yield f"data: {json.dumps({'type': 'done', 'status': job['status'], 'result': job['result']})}\n\n"
                break
            await asyncio.sleep(0.3)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/screenshots/{path:path}")
async def serve_screenshot(path: str):
    """Serve a screenshot file from the output directory."""
    file_path = OUTPUT_DIR / path
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, "Screenshot not found")
    return FileResponse(file_path)


# ── Background job runner ─────────────────────────────────────

async def _run_job(job_id: str, req: FetchRequest) -> None:
    """Execute the fetch in background and update job store."""
    from portal_fetcher.cli import _run_fetch

    def on_progress(msg: str) -> None:
        jobs[job_id]["progress"].append(msg)

    try:
        result = await _run_fetch(
            portal=req.portal,
            portal_url=req.portal_url,
            login_user=req.login_user,
            login_pass=req.login_pass,
            subscriber=req.subscriber,
            output_dir=str(OUTPUT_DIR),
            headless=req.headless,
            timeout=req.timeout,
            on_progress=on_progress,
        )
        result_dict = json.loads(result.model_dump_json())
        # Convert absolute screenshot paths to relative URLs for the web UI
        if result_dict.get("screenshots"):
            output_abs = str(OUTPUT_DIR.resolve())
            result_dict["screenshot_urls"] = []
            for s in result_dict["screenshots"]:
                rel = Path(s).relative_to(OUTPUT_DIR.resolve())
                result_dict["screenshot_urls"].append(f"/screenshots/{rel}")
        jobs[job_id]["result"] = result_dict
        jobs[job_id]["status"] = "completed" if result.success else "failed"
    except Exception as exc:
        jobs[job_id]["result"] = {"error": f"{type(exc).__name__}: {exc}"}
        jobs[job_id]["status"] = "failed"
