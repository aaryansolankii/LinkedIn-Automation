"""Daily scheduler tasks for post generation and approval email dispatch."""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from content_generator import generate_post
from email_sender import send_approval_email
from excel_manager import get_all_rows, update_cell

logger = logging.getLogger(__name__)


def _is_due_today(date_value: str, today: date) -> bool:
    """Return True when a sheet date string resolves to today's date."""
    if not date_value.strip():
        return False
    parsed = pd.to_datetime(date_value, errors="coerce")
    if pd.isna(parsed):
        return False
    return parsed.date() == today


def check_daily_posts() -> None:
    """Generate post content for due Excel rows and email owner for approval."""
    logger.info("Scheduler check started.")
    try:
        df = get_all_rows()
    except Exception:
        logger.exception("Failed to read rows from Excel database.")
        return

    if df.empty:
        logger.info("Scheduler check finished. No rows found in Excel database.")
        return

    today = date.today()
    processed_count = 0

    for index, row in df.iterrows():
        row_number = index + 1
        generation_date = str(row.get("generation_date", "")).strip()
        post_content = str(row.get("post_content", "")).strip()

        is_due = _is_due_today(generation_date, today)
        needs_content = not post_content.strip()
        if not (is_due and needs_content):
            continue

        title = str(row.get("title", "")).strip()
        hook = str(row.get("hook", "")).strip()
        about = str(row.get("about", "")).strip()

        try:
            generated_post, image_prompt = generate_post(title=title, hook=hook, about=about)
            from image_generator import generate_and_save_image
            image_path = generate_and_save_image(title=title, topic=about, image_prompt=image_prompt)
            
            update_cell(row_number, "post_content", generated_post)
            update_cell(row_number, "image_path", image_path)
            update_cell(row_number, "approved", "pending")
            send_approval_email(
                row_number=row_number,
                title=title,
                hook=hook,
                generated_post=generated_post,
                image_path=image_path
            )
            processed_count += 1
            logger.info("Generated post content and sent approval for row %s.", row_number)
        except Exception:
            logger.exception("Failed processing row %s.", row_number)

    logger.info("Scheduler check finished. Rows processed: %s", processed_count)


def publish_queued_posts() -> None:
    """Sweep the Excel database for 'queued' posts and publish them to LinkedIn."""
    logger.info("Publishing sweep started. Checking for queued posts.")
    try:
        df = get_all_rows()
    except Exception:
        logger.exception("Failed to read rows from Excel database during publishing sweep.")
        return

    if df.empty:
        return

    published_count = 0
    from linkedin_poster import post_to_linkedin  # Local import to avoid circular issues

    for index, row in df.iterrows():
        row_number = index + 1
        
        approved_status = str(row.get("approved", "")).strip().lower()
        posted_status = str(row.get("posted", "")).strip().lower()
        post_content = str(row.get("post_content", "")).strip()
        image_path = str(row.get("image_path", "")).strip()

        # Only process rows that have been approved (queued for posting) but not yet posted
        if approved_status == "queued" and posted_status != "yes" and post_content:
            logger.info("Publishing queued post on row %s...", row_number)
            try:
                # Attempt to publish
                posted = post_to_linkedin(post_content, image_path)
                if posted:
                    update_cell(row_number, "posted", "yes")
                    update_cell(row_number, "approved", "yes") # update queued to yes
                    published_count += 1
                    logger.info("Successfully published queued row %s.", row_number)
                else:
                    logger.error("Failed to post row %s. Status remains queued.", row_number)
            except Exception:
                logger.exception("Exception occurred while posting row %s.", row_number)

    logger.info("Publishing sweep finished. Posts published: %s", published_count)
