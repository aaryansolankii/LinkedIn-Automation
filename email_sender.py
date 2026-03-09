"""Email notification module for content approval workflow."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import get_settings

logger = logging.getLogger(__name__)


def _build_approval_email_body(row_number: int, title: str, hook: str, generated_post: str) -> str:
    """Create the plain-text approval email body with approve/reject links."""
    approve_link = f"http://localhost:8000/approve?id={row_number}"
    reject_link = f"http://localhost:8000/reject?id={row_number}"
    return f"""LinkedIn Post Approval Request

Row Number: {row_number}

Title:
{title}

Hook:
{hook}

Generated Post:
{generated_post}

Approve:
{approve_link}

Reject:
{reject_link}
"""


def send_approval_email(row_number: int, title: str, hook: str, generated_post: str) -> None:
    """Send a review email for a generated LinkedIn post."""
    settings = get_settings()
    body = _build_approval_email_body(row_number, title, hook, generated_post)

    message = MIMEMultipart("alternative")
    message["Subject"] = f"LinkedIn Post Approval Needed (Row {row_number})"
    message["From"] = settings.email_user
    message["To"] = settings.owner_email
    message.attach(MIMEText(body, "plain", "utf-8"))

    logger.info("Sending approval email for row %s to %s", row_number, settings.owner_email)
    try:
        if settings.email_port == 465:
            with smtplib.SMTP_SSL(settings.email_host, settings.email_port, timeout=30) as server:
                server.login(settings.email_user, settings.email_password)
                server.sendmail(settings.email_user, [settings.owner_email], message.as_string())
        else:
            with smtplib.SMTP(settings.email_host, settings.email_port, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(settings.email_user, settings.email_password)
                server.sendmail(settings.email_user, [settings.owner_email], message.as_string())
    except (smtplib.SMTPException, OSError):
        logger.exception("Failed to send approval email for row %s.", row_number)
        raise

    logger.info("Approval email sent for row %s.", row_number)
