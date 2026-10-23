"""MCP server exposing LinkedIn publishing and a content calendar to Claude."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from importlib.metadata import version
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Image, MCPServer
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from . import auth, cards, linkedin, store
from .config import DATA_DIR, ENV_FILE, get_settings, load_credentials
from .errors import LinkedInError

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
LinkedIn publishing plus a local content calendar (idea -> draft -> scheduled -> published).

Rules:
- Posting is public. Only call publish_post or schedule_post after the user has explicitly approved \
that exact text in this conversation; show the final text first.
- LinkedIn displays plain text only: no markdown (no **bold**, *italics*, headings or [text](url) links). \
Separate paragraphs with a blank line, use "•" for bullets, put hashtags as #Word on the last line. \
Write the text exactly as it should appear; escaping for LinkedIn's API is automatic.

Workflow: plan_posts saves dated ideas -> write a post from an idea and store it with save_post -> \
optionally make_post_image -> after approval, schedule_post (published by publish_due once its time \
arrives) or publish_post (right now).
If a tool says the account is not connected or the token expired, call linkedin_login.
"""

mcp = MCPServer(name="linkedin", version=version("linkedin-mcp"), instructions=INSTRUCTIONS)

READ_ONLY = ToolAnnotations(readOnlyHint=True)
PUBLISHES = ToolAnnotations(destructiveHint=False, openWorldHint=True)

Status = Literal["idea", "draft", "scheduled", "publishing", "published", "failed"]
Theme = Literal["sky", "mint", "peach", "lavender", "midnight"]


class Idea(BaseModel):
    """One planned post."""

    title: str = Field(description="Specific, opinionated post title.")
    hook: str = Field("", description="Opening line that stops the scroll.")
    notes: str = Field("", description="Angle, key points, examples or data to use when writing it.")
    date: str = Field("", description="Planned date 'YYYY-MM-DD'. Leave empty to assign dates in sequence.")


def _parse_date(value: str) -> date:
    """Parse 'YYYY-MM-DD' or explain the expected format."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise LinkedInError(f"Invalid date {value!r}; use 'YYYY-MM-DD'.") from exc


def _summary(post: dict[str, Any]) -> dict[str, Any]:
    """Compact calendar row for a post."""
    row = {
        "id": post["id"],
        "status": post["status"],
        "publish_at": post["publish_at"],
        "title": post["title"] or post["content"][:60],
        "has_content": bool(post["content"]),
        "has_image": bool(post["image_path"]),
    }
    if post["post_urn"]:
        row["url"] = linkedin.post_url(post["post_urn"])
    if post["error"]:
        row["error"] = post["error"]
    return row


def _full(post: dict[str, Any]) -> dict[str, Any]:
    """Every field of a post plus its word count and URL."""
    view = {**post, "words": len(post["content"].split())}
    if post["post_urn"]:
        view["url"] = linkedin.post_url(post["post_urn"])
    return view


def _publish(post: dict[str, Any], *, mark_failed: bool) -> dict[str, Any]:
    """Publish one stored post exactly once and record the outcome."""
    post_id, previous = post["id"], post["status"]
    if previous == "published":
        raise LinkedInError(f"Post {post_id} is already published: {linkedin.post_url(post['post_urn'])}")
    if not post["content"].strip():
        raise LinkedInError(f"Post {post_id} has no content yet. Write it and save it with save_post first.")
    if not store.claim_post(post_id, ("draft", "scheduled", "failed")):
        raise LinkedInError(f"Post {post_id} is {store.get_post(post_id)['status']} and can't be published now.")

    try:
        result = linkedin.publish(post["content"], post["image_path"])
    except Exception as exc:
        message = str(exc) if isinstance(exc, LinkedInError) else f"Unexpected error: {exc!r}"
        logger.exception("Publishing post %s failed", post_id)
        store.update_post(post_id, status="failed" if mark_failed else previous, error=message)
        raise LinkedInError(message) from exc

    store.update_post(post_id, status="published", published_at=store.now(), post_urn=result.urn, error="")
    return {"published": True, "id": post_id, "title": post["title"], "url": result.url}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
def linkedin_status() -> dict[str, Any]:
    """Show whether LinkedIn is connected (and as whom), when the token expires, calendar totals and the current local time."""
    settings = get_settings()
    credentials = load_credentials()
    account: dict[str, Any] = {"connected": False}
    if credentials is None:
        account["next_step"] = (
            "Call linkedin_login."
            if settings.client_id and settings.client_secret
            else f"Add LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET to {ENV_FILE}, then call linkedin_login."
        )
    elif credentials.is_expired():
        account.update(problem=f"Token expired on {credentials.expires_at}.", next_step="Call linkedin_login.")
    else:
        account.update(author_urn=credentials.author_urn, token_expires_at=credentials.expires_at or "unknown")
        if credentials.expires_at:
            account["token_days_left"] = (datetime.fromisoformat(credentials.expires_at) - datetime.now()).days
        try:
            profile = linkedin.get_profile(credentials.access_token)
            account.update(connected=True, name=profile.get("name") or credentials.name)
        except LinkedInError as exc:
            if exc.status_code == 401:
                account.update(problem="Token is invalid or revoked.", next_step="Call linkedin_login.")
            else:
                account.update(connected="unverified", problem=str(exc))

    return {
        "now": store.now(),
        "account": account,
        "calendar": store.status_counts(),
        "scheduled_and_due": len(store.due_posts(store.now())),
        "default_posting_time": settings.posting_time,
        "data_dir": str(DATA_DIR),
    }


@mcp.tool(annotations=ToolAnnotations(openWorldHint=True))
def linkedin_login() -> dict[str, str]:
    """Connect a LinkedIn account: opens LinkedIn's consent page in the browser and saves the token once the user approves. Tokens last about 60 days; log in again when one expires."""
    flow = auth.start_login()
    return {
        "status": "waiting_for_browser",
        "login_url": flow.url,
        "next_step": "Ask the user to approve access in the browser tab that opened (or to open login_url), "
        "then call linkedin_status to confirm.",
    }


@mcp.tool()
def plan_posts(
    ideas: list[Idea],
    start_date: Annotated[str, Field(description="First date for undated ideas, 'YYYY-MM-DD'. Default: tomorrow.")] = "",
    every_days: Annotated[int, Field(ge=1, le=30, description="Days between consecutive undated ideas.")] = 1,
) -> dict[str, Any]:
    """Add post ideas to the content calendar. Come up with the ideas yourself; undated ones get one slot every `every_days` days from `start_date`."""
    posting_time = get_settings().posting_time
    next_day = _parse_date(start_date) if start_date else date.today() + timedelta(days=1)
    added = []
    for idea in ideas:
        day = idea.date
        if not day:
            day = next_day.isoformat()
            next_day += timedelta(days=every_days)
        post = store.add_post(
            title=idea.title.strip(),
            hook=idea.hook.strip(),
            notes=idea.notes.strip(),
            status="idea",
            publish_at=store.normalize_time(day, posting_time),
        )
        added.append(_summary(post))
    return {"added": len(added), "posts": added}


@mcp.tool(annotations=READ_ONLY)
def list_posts(
    status: Annotated[Status | None, Field(description="Only posts with this status. Default: everything not yet published.")] = None,
    limit: Annotated[int, Field(ge=1, le=500)] = 50,
) -> dict[str, Any]:
    """List the content calendar ordered by planned publish time (overdue items first)."""
    posts = store.list_posts(status, limit)
    return {"now": store.now(), "count": len(posts), "posts": [_summary(post) for post in posts]}


@mcp.tool(annotations=READ_ONLY)
def get_post(post_id: int) -> dict[str, Any]:
    """Get every field of one post, including its full text."""
    return _full(store.get_post(post_id))


@mcp.tool()
def save_post(
    post_id: Annotated[int | None, Field(description="Post to update. Omit to create a new post.")] = None,
    content: Annotated[str | None, Field(description="Full post text exactly as it should appear on LinkedIn.")] = None,
    title: str | None = None,
    hook: str | None = None,
    notes: str | None = None,
    image_path: Annotated[str | None, Field(description="Local PNG/JPG/GIF to attach; empty string removes the image.")] = None,
    publish_at: Annotated[str | None, Field(description="Planned time 'YYYY-MM-DD HH:MM' (local) or just a date.")] = None,
) -> dict[str, Any]:
    """Create or update a post. A post with content is a draft. Saving a scheduled post moves it back to draft until schedule_post is called again."""
    fields = {
        name: value.strip()
        for name, value in {"content": content, "title": title, "hook": hook, "notes": notes}.items()
        if value is not None
    }
    if image_path is not None:
        fields["image_path"] = str(linkedin.check_image(image_path)) if image_path.strip() else ""
    if publish_at is not None:
        fields["publish_at"] = store.normalize_time(publish_at, get_settings().posting_time)

    if post_id is None:
        if not fields.get("content") and not fields.get("title"):
            raise LinkedInError("Nothing to save: pass content or at least a title.")
        return _full(store.add_post(**fields, status="draft" if fields.get("content") else "idea"))

    current = store.get_post(post_id)
    if current["status"] in ("publishing", "published"):
        raise LinkedInError(f"Post {post_id} is {current['status']} and can no longer be edited here.")
    has_content = bool(fields.get("content", current["content"]))
    return _full(store.update_post(post_id, **fields, status="draft" if has_content else "idea", error=""))


@mcp.tool()
def schedule_post(
    post_id: int,
    publish_at: Annotated[str | None, Field(description="When to publish, 'YYYY-MM-DD HH:MM' local time. Default: the post's planned time.")] = None,
) -> dict[str, Any]:
    """Approve a draft for automatic publishing at publish_at. Only call after the user approved this exact text. Due posts are published by publish_due."""
    post = store.get_post(post_id)
    if post["status"] not in ("draft", "scheduled", "failed"):
        raise LinkedInError(f"Post {post_id} is {post['status']}; only drafts can be scheduled.")
    if not post["content"]:
        raise LinkedInError(f"Post {post_id} has no text yet. Write it and save it with save_post first.")
    when = store.normalize_time(publish_at, get_settings().posting_time) if publish_at else post["publish_at"]
    if not when:
        raise LinkedInError("Pass publish_at as 'YYYY-MM-DD HH:MM'.")
    if when <= store.now():
        raise LinkedInError(f"{when} is in the past. Pass a future publish_at, or use publish_post to post right now.")
    post = store.update_post(post_id, status="scheduled", publish_at=when, error="")
    return {
        "scheduled": _summary(post),
        "note": "publish_due posts it once publish_at arrives. It runs from the OS scheduled task "
        "(see README) or whenever publish_due is called.",
    }


@mcp.tool(annotations=PUBLISHES)
def publish_post(post_id: int) -> dict[str, Any]:
    """Publish a saved post to LinkedIn right now (public). Only call after the user explicitly approved this exact text."""
    return _publish(store.get_post(post_id), mark_failed=False)


@mcp.tool(annotations=PUBLISHES)
def publish_due() -> dict[str, Any]:
    """Publish every scheduled post whose publish_at has arrived. A post that fails is marked failed with the error; reschedule it to retry."""
    results = []
    for post in store.due_posts(store.now()):
        try:
            results.append(_publish(post, mark_failed=True))
        except LinkedInError as exc:
            results.append({"published": False, "id": post["id"], "title": post["title"], "error": str(exc)})
    return {"now": store.now(), "processed": len(results), "results": results}


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def delete_post(post_id: int) -> dict[str, Any]:
    """Delete a post from the local calendar. A post already published stays on LinkedIn."""
    post = store.get_post(post_id)
    if post["status"] == "publishing":
        raise LinkedInError(f"Post {post_id} is being published right now; try again in a moment.")
    store.delete_post(post_id)
    return {"deleted": post_id, "title": post["title"]}


@mcp.tool(structured_output=False)
def make_post_image(
    title: Annotated[str, Field(description="Headline for the card; under 10 words reads best.")],
    subtitle: Annotated[str, Field(description="Optional supporting line under the headline.")] = "",
    eyebrow: Annotated[str, Field(description="Optional small label above the headline, e.g. the topic.")] = "",
    author: Annotated[str, Field(description="Optional name shown at the bottom, e.g. the user's name.")] = "",
    theme: Theme = "sky",
    post_id: Annotated[int | None, Field(description="Attach the image to this post.")] = None,
) -> list[Any]:
    """Render a square title-card image (soft gradient, bold headline) and optionally attach it to a post. Returns a preview."""
    path = cards.render_title_card(title.strip(), subtitle.strip(), eyebrow.strip(), author.strip(), theme)
    message = f"Saved {path}"
    if post_id is not None:
        save_post(post_id=post_id, image_path=str(path))
        message += f" and attached it to post {post_id}"
    return [message, Image(data=cards.preview_jpeg(path), format="jpeg")]
