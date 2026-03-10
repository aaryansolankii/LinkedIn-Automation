"""Email notification module for content approval workflow."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

from config import get_settings

logger = logging.getLogger(__name__)


def _build_approval_email_html(row_number: int, title: str, hook: str, generated_post: str, has_image: bool) -> str:
    """Create the HTML approval email body with approve/reject links."""
    approve_link = f"http://localhost:8000/approve?id={row_number}"
    reject_link = f"http://localhost:8000/reject?id={row_number}"
    
    # We replace newlines with <br> for HTML
    formatted_post = generated_post.replace('\n', '<br>')
    
    img_tag = '<br><h3>Generated Image:</h3><img src="cid:post_image" style="max-width: 600px; border-radius: 8px;">' if has_image else ''

    return f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
        <h2>LinkedIn Post Approval Request</h2>
        <p><strong>Row Number:</strong> {row_number}</p>
        <p><strong>Title:</strong><br/>{title}</p>
        <p><strong>Hook:</strong><br/>{hook}</p>
        <hr/>
        <h3>Generated Post:</h3>
        <p style="background: #f4f4f4; padding: 15px; border-radius: 5px;">
            {formatted_post}
        </p>
        {img_tag}
        <hr/>
        <p style="margin-top: 20px;">
            <a href="{approve_link}" style="background-color: #28a745; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; margin-right: 15px;">Approve</a>
            <a href="{reject_link}" style="background-color: #dc3545; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold;">Reject & Regenerate</a>
        </p>
      </body>
    </html>
    """


def send_approval_email(row_number: int, title: str, hook: str, generated_post: str, image_path: str = "") -> None:
    """Send a review email for a generated LinkedIn post."""
    settings = get_settings()
    has_image = bool(image_path and image_path.strip())
    body = _build_approval_email_html(row_number, title, hook, generated_post, has_image)

    # Use 'related' to allow inline images
    message = MIMEMultipart("related")
    message["Subject"] = f"LinkedIn Post Approval Needed (Row {row_number})"
    message["From"] = settings.email_user
    message["To"] = settings.owner_email
    
    # Attach HTML body
    msg_alt = MIMEMultipart("alternative")
    message.attach(msg_alt)
    msg_alt.attach(MIMEText("Please enable HTML to view this message.", "plain", "utf-8"))
    msg_alt.attach(MIMEText(body, "html", "utf-8"))
    
    # Attach Image
    if has_image:
        try:
            with open(image_path, "rb") as f:
                img_data = f.read()
            mime_img = MIMEImage(img_data)
            mime_img.add_header('Content-ID', '<post_image>')
            message.attach(mime_img)
        except Exception:
            logger.exception("Failed to attach image to approval email.")

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
