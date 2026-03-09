"""FastAPI backend unifying idea seeding, scheduling, and approval workflows."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Any

import schedule
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from config import get_settings, setup_logging
from content_generator import generate_post
from email_sender import send_approval_email
from excel_manager import append_row, get_all_rows, update_cell
from linkedin_poster import post_to_linkedin
from llm_generator import generate_ideas
from scheduler import check_daily_posts

logger = logging.getLogger(__name__)


# --- Background Scheduler Logic ---

def _run_scheduler_loop() -> None:
    """Run daily scheduled checks in a blocking loop (should be run in a thread)."""
    settings = get_settings()
    schedule.clear("linkedin-daily-check")
    schedule.every().day.at(settings.scheduler_time).do(check_daily_posts).tag("linkedin-daily-check")
    
    from scheduler import publish_queued_posts
    schedule.every().day.at(settings.posting_time).do(publish_queued_posts).tag("linkedin-daily-post")
    
    logger.info("Scheduler loop started. Prep time: %s | Post time: %s", settings.scheduler_time, settings.posting_time)

    # Run an immediate check at startup, then continue with daily schedule.
    try:
        check_daily_posts()
    except Exception:
        logger.exception("Failed initial startup post check.")

    while True:
        schedule.run_pending()
        time.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    """Manage application lifecycle: start scheduler thread on startup."""
    setup_logging()
    
    # Start the scheduler loop in a daemon thread so it runs alongside the API
    scheduler_thread = threading.Thread(target=_run_scheduler_loop, daemon=True)
    scheduler_thread.start()
    
    logger.info("FastAPI application started. Background scheduler thread launched.")
    yield
    logger.info("FastAPI application shutting down.")


# --- FastAPI Application ---

app = FastAPI(
    title="LinkedIn Content Automation & Approval API",
    version="1.0.0",
    lifespan=lifespan,
)


def _read_row_or_404(row_id: int) -> dict:
    """Fetch a row by 1-based index and raise HTTP 404 when missing."""
    try:
        df = get_all_rows()
    except Exception as exc:
        logger.exception("Failed to read Excel database.")
        raise HTTPException(status_code=500, detail="Failed to read Excel database.") from exc

    zero_based_index = row_id - 1
    if zero_based_index < 0 or zero_based_index >= len(df):
        raise HTTPException(status_code=404, detail=f"Row {row_id} was not found.")

    row = df.iloc[zero_based_index].to_dict()
    return {key: str(value).strip() for key, value in row.items()}


# --- API Endpoints ---

@app.get("/health")
def health() -> dict[str, str]:
    """Return a lightweight health status for uptime checks."""
    return {"status": "ok"}


@app.get("/approve")
def approve_post(id: int = Query(..., ge=1)) -> dict[str, str | int]:
    """Approve a row's generated content to be published at the daily schedule time."""
    row = _read_row_or_404(id)
    post_content = row.get("post_content", "").strip()
    if not post_content:
        raise HTTPException(status_code=400, detail="No generated content found for this row.")

    try:
        # Instead of posting right away, mark it as 'queued'
        update_cell(id, "approved", "queued")
    except Exception as exc:
        logger.exception("Failed to update approval status for row %s.", id)
        raise HTTPException(status_code=500, detail="Failed to update Excel status.") from exc

    logger.info("Row %s approved and queued for scheduled publishing.", id)
    return {"status": "success", "row": id, "message": "Approved and queued for scheduled publishing."}



@app.get("/reject")
def reject_post(id: int = Query(..., ge=1)) -> dict[str, str | int]:
    """Reject content, regenerate post text, and send a new approval email."""
    row = _read_row_or_404(id)
    title = row.get("title", "").strip()
    hook = row.get("hook", "").strip()
    about = row.get("about", "").strip()

    try:
        update_cell(id, "approved", "rejected")
        regenerated_post = generate_post(title=title, hook=hook, about=about)
        update_cell(id, "post_content", regenerated_post)
        send_approval_email(
            row_number=id,
            title=title,
            hook=hook,
            generated_post=regenerated_post,
        )
    except Exception as exc:
        logger.exception("Failed rejection-regeneration flow for row %s.", id)
        raise HTTPException(status_code=500, detail="Reject flow failed.") from exc

    logger.info("Row %s rejected, regenerated, and re-sent for approval.", id)
    return {
        "status": "success",
        "row": id,
        "message": "Rejected, regenerated, and sent for approval again.",
    }


class SeedRequest(BaseModel):
    niche: str
    days: int = 30


@app.post("/api/seed-ideas")
def seed_ideas_endpoint(request: SeedRequest) -> dict[str, Any]:
    """Generate idea rows with future dates and insert them into Excel via API."""
    logger.info("Starting API idea generation for niche: %s", request.niche)
    try:
        ideas = generate_ideas(niche=request.niche, days=request.days)
        start_date = date.today()
        inserted_rows = 0

        for offset, idea in enumerate(ideas):
            generation_date = (start_date + timedelta(days=offset)).isoformat()
            append_row(
                title=idea["title"],
                hook=idea["hook"],
                about=idea["about"],
                generation_date=generation_date,
            )
            inserted_rows += 1

        logger.info("Seeded %s ideas for niche '%s'.", inserted_rows, request.niche)
        return {"status": "success", "inserted_rows": inserted_rows, "niche": request.niche}
    except Exception as exc:
        logger.exception("Failed to seed ideas.")
        raise HTTPException(status_code=500, detail="Failed to generate/seed ideas.") from exc


@app.post("/api/trigger-scheduler")
def trigger_scheduler_endpoint() -> dict[str, str]:
    """Manually trigger the daily scheduled check for testing."""
    logger.info("Manual scheduler check triggered via API.")
    try:
        check_daily_posts()
        return {"status": "success", "message": "Scheduler check ran successfully."}
    except Exception as exc:
        logger.exception("Failed manual scheduler check.")
        raise HTTPException(status_code=500, detail="Failed to run scheduler check.") from exc


def _validate_python_version() -> None:
    """Enforce Python 3.11+ runtime requirement."""
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or newer is required.")


if __name__ == "__main__":
    _validate_python_version()
    parser = argparse.ArgumentParser(description="LinkedIn Automation Backend")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host address")
    parser.add_argument("--port", type=int, default=8000, help="Port to run the API on")
    args = parser.parse_args()

    # Start the unified backend application
    uvicorn.run("main:app", host=args.host, port=args.port, reload=True)
