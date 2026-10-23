"""Command line: run the MCP server (default), log in, check status, or publish due posts."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from .config import DATA_DIR, LOG_FILE
from .errors import LinkedInError


def _setup_logging(to_file: bool) -> None:
    """Log to stderr (stdout carries the MCP protocol) and, locally, to a small rotating file."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if to_file:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=1, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=handlers,
    )


def main() -> None:
    """Entry point for the `linkedin-mcp` command."""
    parser = argparse.ArgumentParser(prog="linkedin-mcp", description="LinkedIn tools for Claude.")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="run the MCP server over stdio (default)")
    commands.add_parser("serve-http", help="run the hosted MCP server over streamable HTTP on $PORT")
    commands.add_parser("login", help="connect a LinkedIn account in the browser")
    commands.add_parser("status", help="show the connection and calendar status")
    commands.add_parser("publish-due", help="publish scheduled posts whose time has come (for a scheduled task)")
    args = parser.parse_args()
    _setup_logging(to_file=args.command != "serve-http")

    from . import auth, server

    try:
        match args.command:
            case None | "serve":
                server.mcp.run()
            case "serve-http":
                import uvicorn

                from .http_app import build_app

                # access_log=False: request paths contain the MCP secret and must never be logged.
                uvicorn.run(
                    build_app(),
                    host="0.0.0.0",
                    port=int(os.environ.get("PORT", "8080")),
                    access_log=False,
                    proxy_headers=True,
                    forwarded_allow_ips="*",
                )
            case "login":
                flow = auth.start_local_login()
                print(f"Approve access in your browser. If it didn't open, visit:\n{flow.url}\n")
                credentials = flow.wait()
                print(f"Connected as {credentials.name or credentials.author_urn} (token expires {credentials.expires_at}).")
            case "status":
                print(json.dumps(server.linkedin_status(), indent=2))
            case "publish-due":
                result = server.publish_due()
                print(json.dumps(result, indent=2))
                if any(not item["published"] for item in result["results"]):
                    sys.exit(1)
    except LinkedInError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
