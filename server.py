"""
Gmail Multi-Account MCP Server
-------------------------------
Exposes Gmail operations for multiple Google accounts via the
Model Context Protocol (MCP) stdio transport.

Start with:  python server.py
Configure accounts in config.json and authenticate with: python setup_auth.py
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from auth import AuthManager
from config import get_accounts, get_client_secret_path, get_credentials_dir, load_config, save_config
from gcalendar import CalendarService
from gdrive import DriveService
from gmail import GmailService

# ---------------------------------------------------------------------------
# Bootstrap: load config and auth manager at startup
# ---------------------------------------------------------------------------

try:
    _config = load_config()
    _accounts = get_accounts(_config)
    _credentials_dir = get_credentials_dir(_config)
    _client_secret_path = get_client_secret_path(_config)
    _auth = AuthManager(_credentials_dir, _client_secret_path)
except FileNotFoundError as exc:
    print(f"STARTUP ERROR: {exc}", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_creds(account_name: str):
    """Return valid credentials for an account or raise ValueError."""
    if account_name not in _accounts:
        raise ValueError(
            f"Unknown account '{account_name}'. Available: {list(_accounts.keys())}"
        )
    creds = _auth.get_credentials(account_name)
    if creds is None:
        email = _accounts[account_name].get("email", account_name)
        raise ValueError(
            f"Account '{account_name}' ({email}) is not authenticated. "
            "Run 'python setup_auth.py' to authenticate."
        )
    return creds


def _get_service(account_name: str) -> GmailService:
    return GmailService(_get_creds(account_name), account_name)


def _get_calendar(account_name: str) -> CalendarService:
    return CalendarService(_get_creds(account_name), account_name)


def _get_drive(account_name: str) -> DriveService:
    return DriveService(_get_creds(account_name), account_name)


def _fmt(data: Any) -> list[types.TextContent]:
    if isinstance(data, str):
        return [types.TextContent(type="text", text=data)]
    return [types.TextContent(type="text", text=json.dumps(data, indent=2, ensure_ascii=False))]


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

server = Server("gmail-multi-account")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_accounts",
            description=(
                "List all Gmail accounts configured in this MCP server, "
                "along with their authentication status."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="link_account",
            description=(
                "Link (authenticate) a Gmail account via OAuth. "
                "If 'account' is omitted, lists all accounts and asks which one to link. "
                "Opens a browser window for the user to sign in."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {
                        "type": "string",
                        "description": "Account name to link (e.g. 'personal', 'work'). Omit to see available accounts.",
                    }
                },
            },
        ),
        types.Tool(
            name="unlink_account",
            description=(
                "Unlink (revoke local credentials for) a Gmail account. "
                "If 'account' is omitted, lists all currently linked accounts and asks which one to unlink."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {
                        "type": "string",
                        "description": "Account name to unlink (e.g. 'personal', 'work'). Omit to see linked accounts.",
                    }
                },
            },
        ),
        types.Tool(
            name="add_account",
            description=(
                "Add a new Gmail account to this MCP server's config and immediately "
                "trigger OAuth so it is ready to use. "
                "Returns an error if an account with that name already exists."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {
                        "type": "string",
                        "description": "Short name/key for the account (e.g. 'personal', 'work2'). Must be unique.",
                    },
                    "email": {
                        "type": "string",
                        "description": "Gmail address for this account (e.g. 'you@gmail.com').",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional human-readable label (e.g. 'Side project account').",
                    },
                },
                "required": ["account", "email"],
            },
        ),
        types.Tool(
            name="remove_account",
            description=(
                "Remove a Gmail account from this MCP server's config, revoke its local "
                "credentials, and delete its entry from config.json. "
                "If 'account' is omitted, lists all configured accounts and asks which to remove."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {
                        "type": "string",
                        "description": "Account name to remove. Omit to see all configured accounts.",
                    }
                },
            },
        ),
        types.Tool(
            name="gmail_get_profile",
            description="Get the Gmail profile (email address, message count, thread count) for an account.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {
                        "type": "string",
                        "description": "Account name as defined in config.json (e.g. 'personal', 'work')",
                    }
                },
                "required": ["account"],
            },
        ),
        types.Tool(
            name="gmail_search",
            description=(
                "Search emails using Gmail search syntax. "
                "Searches a single account or all accounts if 'account' is omitted. "
                "Example queries: 'from:boss@company.com is:unread', 'subject:invoice has:attachment'"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {
                        "type": "string",
                        "description": "Account to search. Omit to search all configured accounts.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Gmail search query string",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max results per account (default 10, max 50)",
                        "default": 10,
                    },
                    "include_body": {
                        "type": "boolean",
                        "description": "Include full message body in results (slower). Default: false.",
                        "default": False,
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="gmail_read_message",
            description="Read the full content of a Gmail message by its ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {
                        "type": "string",
                        "description": "Account that owns the message",
                    },
                    "message_id": {
                        "type": "string",
                        "description": "Gmail message ID (from search results)",
                    },
                },
                "required": ["account", "message_id"],
            },
        ),
        types.Tool(
            name="gmail_read_thread",
            description="Read all messages in a Gmail thread/conversation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {
                        "type": "string",
                        "description": "Account that owns the thread",
                    },
                    "thread_id": {
                        "type": "string",
                        "description": "Gmail thread ID",
                    },
                },
                "required": ["account", "thread_id"],
            },
        ),
        types.Tool(
            name="gmail_send",
            description="Send an email from a specific Gmail account.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {
                        "type": "string",
                        "description": "Account to send from",
                    },
                    "to": {
                        "type": "string",
                        "description": "Recipient(s), comma-separated",
                    },
                    "subject": {"type": "string", "description": "Email subject"},
                    "body": {"type": "string", "description": "Email body (plain text)"},
                    "cc": {"type": "string", "description": "CC recipients, comma-separated"},
                    "bcc": {"type": "string", "description": "BCC recipients, comma-separated"},
                    "thread_id": {
                        "type": "string",
                        "description": "Thread ID to reply into (threadId from search results). Nests this message into an existing conversation.",
                    },
                    "in_reply_to": {
                        "type": "string",
                        "description": "Message-ID of the message being replied to (the 'message-id' field from gmail_read_message). Sets the In-Reply-To MIME header.",
                    },
                    "references": {
                        "type": "string",
                        "description": "Space-separated chain of Message-IDs for the thread (usually same as in_reply_to for a direct reply). Sets the References MIME header.",
                    },
                },
                "required": ["account", "to", "subject", "body"],
            },
        ),
        types.Tool(
            name="gmail_create_draft",
            description="Save an email as a draft in a specific Gmail account.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {
                        "type": "string",
                        "description": "Account to create the draft in",
                    },
                    "to": {"type": "string", "description": "Recipient(s), comma-separated"},
                    "subject": {"type": "string", "description": "Email subject"},
                    "body": {"type": "string", "description": "Email body (plain text)"},
                    "cc": {"type": "string", "description": "CC recipients"},
                    "bcc": {"type": "string", "description": "BCC recipients"},
                    "thread_id": {
                        "type": "string",
                        "description": "Thread ID to reply into (threadId from search results). Nests this draft into an existing conversation.",
                    },
                    "in_reply_to": {
                        "type": "string",
                        "description": "Message-ID of the message being replied to (the 'message-id' field from gmail_read_message). Sets the In-Reply-To MIME header.",
                    },
                    "references": {
                        "type": "string",
                        "description": "Space-separated chain of Message-IDs for the thread (usually same as in_reply_to for a direct reply). Sets the References MIME header.",
                    },
                },
                "required": ["account", "to", "subject", "body"],
            },
        ),
        types.Tool(
            name="gmail_send_draft",
            description="Send an existing draft email by its draft ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {
                        "type": "string",
                        "description": "Account that owns the draft",
                    },
                    "draft_id": {
                        "type": "string",
                        "description": "Draft ID (from gmail_list_drafts)",
                    },
                },
                "required": ["account", "draft_id"],
            },
        ),
        types.Tool(
            name="gmail_list_drafts",
            description="List draft emails in a Gmail account.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Account name"},
                    "max_results": {
                        "type": "integer",
                        "description": "Max drafts to return (default 10)",
                        "default": 10,
                    },
                },
                "required": ["account"],
            },
        ),
        types.Tool(
            name="gmail_list_labels",
            description="List all labels and folders in a Gmail account.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Account name"}
                },
                "required": ["account"],
            },
        ),
        types.Tool(
            name="gmail_modify_labels",
            description=(
                "Add or remove labels on a Gmail message. "
                "Common label IDs: STARRED, UNREAD, INBOX, SPAM, TRASH, IMPORTANT."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Account name"},
                    "message_id": {"type": "string", "description": "Gmail message ID"},
                    "add_labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Label IDs to add (e.g. ['STARRED', 'UNREAD'])",
                    },
                    "remove_labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Label IDs to remove (e.g. ['UNREAD'])",
                    },
                },
                "required": ["account", "message_id"],
            },
        ),
        types.Tool(
            name="gmail_trash",
            description="Move a Gmail message to the Trash.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Account name"},
                    "message_id": {"type": "string", "description": "Gmail message ID"},
                },
                "required": ["account", "message_id"],
            },
        ),
        # ── Calendar tools ──────────────────────────────────────────────────
        types.Tool(
            name="calendar_list_calendars",
            description="List all Google Calendars available for an account (primary, work, shared, etc.).",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Account name"},
                },
                "required": ["account"],
            },
        ),
        types.Tool(
            name="calendar_list_events",
            description=(
                "List upcoming calendar events for an account. "
                "Optionally filter by time range and calendar. "
                "Times must be in RFC3339 format, e.g. '2026-03-10T00:00:00Z'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Account name"},
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID (default: 'primary'). Use calendar_list_calendars to get IDs.",
                        "default": "primary",
                    },
                    "time_min": {
                        "type": "string",
                        "description": "Start of range (RFC3339). Defaults to now.",
                    },
                    "time_max": {
                        "type": "string",
                        "description": "End of range (RFC3339). Optional.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max events to return (default 20, max 50)",
                        "default": 20,
                    },
                },
                "required": ["account"],
            },
        ),
        types.Tool(
            name="calendar_search",
            description="Search for events by keyword across a calendar (title, description, location, attendees).",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Account name"},
                    "query": {"type": "string", "description": "Search keyword(s)"},
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID (default: 'primary')",
                        "default": "primary",
                    },
                    "time_min": {
                        "type": "string",
                        "description": "Start of range (RFC3339). Defaults to now.",
                    },
                    "time_max": {
                        "type": "string",
                        "description": "End of range (RFC3339). Optional.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max results (default 20)",
                        "default": 20,
                    },
                },
                "required": ["account", "query"],
            },
        ),
        types.Tool(
            name="calendar_get_event",
            description="Get full details of a specific calendar event by its ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Account name"},
                    "event_id": {"type": "string", "description": "Event ID (from list or search results)"},
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID (default: 'primary')",
                        "default": "primary",
                    },
                },
                "required": ["account", "event_id"],
            },
        ),
        types.Tool(
            name="calendar_create_event",
            description="Create a new event on a Google Calendar.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Account name"},
                    "title": {"type": "string", "description": "Event title/summary"},
                    "start_time": {
                        "type": "string",
                        "description": "Start time in RFC3339 format, e.g. '2026-05-21T19:30:00Z'",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "End time in RFC3339 format, e.g. '2026-05-21T20:30:00Z'",
                    },
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID (default: 'primary')",
                        "default": "primary",
                    },
                    "description": {"type": "string", "description": "Event description"},
                    "location": {"type": "string", "description": "Event location"},
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of attendee email addresses",
                    },
                },
                "required": ["account", "title", "start_time", "end_time"],
            },
        ),
        types.Tool(
            name="calendar_update_event",
            description="Update an existing calendar event. Only provided fields are changed.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Account name"},
                    "event_id": {"type": "string", "description": "Event ID to update"},
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID (default: 'primary')",
                        "default": "primary",
                    },
                    "title": {"type": "string", "description": "New event title"},
                    "start_time": {
                        "type": "string",
                        "description": "New start time in RFC3339 format",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "New end time in RFC3339 format",
                    },
                    "description": {"type": "string", "description": "New event description"},
                    "location": {"type": "string", "description": "New event location"},
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Replacement list of attendee email addresses",
                    },
                },
                "required": ["account", "event_id"],
            },
        ),
        types.Tool(
            name="calendar_delete_event",
            description="Delete a calendar event permanently.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Account name"},
                    "event_id": {"type": "string", "description": "Event ID to delete"},
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID (default: 'primary')",
                        "default": "primary",
                    },
                },
                "required": ["account", "event_id"],
            },
        ),
        types.Tool(
            name="calendar_sync_blocks",
            description=(
                "Sync busy blocks from one calendar account to another. "
                "For every event on the source account, a block labelled "
                "'[source] Booked at HH:MM AM/PM' is created on the target account "
                "so that the time slot appears occupied and conflicts are visible."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "source_account": {
                        "type": "string",
                        "description": "Account whose events will be mirrored (e.g. 'personal')",
                    },
                    "target_account": {
                        "type": "string",
                        "description": "Account that receives the block events (e.g. 'work')",
                    },
                    "time_min": {
                        "type": "string",
                        "description": "Start of the window to sync (ISO 8601). Defaults to now.",
                    },
                    "time_max": {
                        "type": "string",
                        "description": "End of the window to sync (ISO 8601). Optional.",
                    },
                    "source_calendar_id": {
                        "type": "string",
                        "description": "Calendar ID on the source account (default: 'primary')",
                        "default": "primary",
                    },
                    "target_calendar_id": {
                        "type": "string",
                        "description": "Calendar ID on the target account (default: 'primary')",
                        "default": "primary",
                    },
                },
                "required": ["source_account", "target_account"],
            },
        ),
        # ── Drive tools ─────────────────────────────────────────────────────
        types.Tool(
            name="drive_list_files",
            description="List files in Google Drive for an account, ordered by most recently modified.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Account name"},
                    "max_results": {"type": "integer", "description": "Max files to return (default 20)", "default": 20},
                    "folder_id": {"type": "string", "description": "Limit to files inside this folder ID. Omit for all files."},
                    "mime_type": {"type": "string", "description": "Filter by MIME type, e.g. 'application/vnd.google-apps.document'"},
                },
                "required": ["account"],
            },
        ),
        types.Tool(
            name="drive_search",
            description="Search Google Drive files by name or content across an account.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Account name"},
                    "query": {"type": "string", "description": "Search keyword(s) — matches file names and full text"},
                    "max_results": {"type": "integer", "description": "Max results (default 20)", "default": 20},
                },
                "required": ["account", "query"],
            },
        ),
        types.Tool(
            name="drive_list_folders",
            description="List all folders in Google Drive for an account.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Account name"},
                    "max_results": {"type": "integer", "description": "Max folders to return (default 20)", "default": 20},
                },
                "required": ["account"],
            },
        ),
        types.Tool(
            name="drive_get_file",
            description="Get metadata for a specific Google Drive file by its ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Account name"},
                    "file_id": {"type": "string", "description": "Google Drive file ID"},
                },
                "required": ["account", "file_id"],
            },
        ),
        types.Tool(
            name="drive_read_file",
            description=(
                "Read the text content of a Google Drive file. "
                "Google Docs export as plain text, Sheets as CSV, Slides as plain text. "
                "Binary files (images, PDFs) are not readable — use drive_download_file instead."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Account name"},
                    "file_id": {"type": "string", "description": "Google Drive file ID"},
                },
                "required": ["account", "file_id"],
            },
        ),
        types.Tool(
            name="drive_download_file",
            description="Download the raw content of a Google Drive file. Returns UTF-8 text or hex for binary files.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Account name"},
                    "file_id": {"type": "string", "description": "Google Drive file ID"},
                },
                "required": ["account", "file_id"],
            },
        ),
        types.Tool(
            name="drive_delete_file",
            description="Move a Google Drive file to trash.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Account name"},
                    "file_id": {"type": "string", "description": "Google Drive file ID"},
                },
                "required": ["account", "file_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    args = arguments or {}

    try:
        # ---- list_accounts ------------------------------------------------
        if name == "list_accounts":
            result = []
            for acct, info in _accounts.items():
                authenticated = _auth.is_authenticated(acct)
                result.append({
                    "name": acct,
                    "email": info.get("email", ""),
                    "description": info.get("description", ""),
                    "authenticated": authenticated,
                    "status": "ready" if authenticated else "not authenticated — run setup_auth.py",
                })
            return _fmt(result)

        # ---- link_account ------------------------------------------------
        elif name == "link_account":
            account = args.get("account")
            if not account:
                choices = [
                    {
                        "name": acct,
                        "email": info.get("email", ""),
                        "authenticated": _auth.is_authenticated(acct),
                    }
                    for acct, info in _accounts.items()
                ]
                return _fmt({
                    "message": "Which account would you like to link? Please call link_account again with 'account' set to one of the names below.",
                    "available_accounts": choices,
                })
            if account not in _accounts:
                return _fmt(f"Error: Unknown account '{account}'. Available: {list(_accounts.keys())}")
            email = _accounts[account].get("email")
            _auth.authenticate(account, email=email)
            return _fmt({
                "status": "linked",
                "account": account,
                "email": email,
            })

        # ---- unlink_account ----------------------------------------------
        elif name == "unlink_account":
            account = args.get("account")
            if not account:
                linked = [
                    {"name": acct, "email": _accounts[acct].get("email", "")}
                    for acct in _accounts
                    if _auth.is_authenticated(acct)
                ]
                if not linked:
                    return _fmt("No accounts are currently linked.")
                return _fmt({
                    "message": "Which account would you like to unlink? Please call unlink_account again with 'account' set to one of the names below.",
                    "linked_accounts": linked,
                })
            if account not in _accounts:
                return _fmt(f"Error: Unknown account '{account}'. Available: {list(_accounts.keys())}")
            removed = _auth.revoke_credentials(account)
            if removed:
                return _fmt({
                    "status": "unlinked",
                    "account": account,
                    "email": _accounts[account].get("email", ""),
                })
            return _fmt(f"Account '{account}' was not linked (no credentials found).")

        # ---- add_account -------------------------------------------------
        elif name == "add_account":
            account = args["account"].strip()
            email = args["email"].strip()
            description = args.get("description", "").strip()

            if account in _accounts:
                return _fmt(
                    f"Error: Account '{account}' already exists "
                    f"(email: {_accounts[account].get('email', '?')}). "
                    "Use a different name or remove the existing account first."
                )

            entry: dict = {"email": email}
            if description:
                entry["description"] = description

            _accounts[account] = entry
            try:
                save_config(_config)
            except Exception as exc:
                del _accounts[account]
                raise RuntimeError(f"Failed to save config: {exc}") from exc

            try:
                _auth.authenticate(account, email=email)
                auth_status = "linked"
                auth_note = "OAuth completed — account is ready to use."
            except Exception as exc:
                auth_status = "not linked"
                auth_note = (
                    f"Account added to config but OAuth did not complete ({exc}). "
                    "Call link_account to authenticate when ready."
                )

            return _fmt({
                "status": "added",
                "account": account,
                "email": email,
                "description": description or None,
                "auth_status": auth_status,
                "note": auth_note,
            })

        # ---- remove_account ----------------------------------------------
        elif name == "remove_account":
            account = args.get("account")

            if not account:
                choices = [
                    {
                        "name": acct,
                        "email": info.get("email", ""),
                        "description": info.get("description", ""),
                        "authenticated": _auth.is_authenticated(acct),
                    }
                    for acct, info in _accounts.items()
                ]
                if not choices:
                    return _fmt("No accounts are configured.")
                return _fmt({
                    "message": (
                        "Which account would you like to remove? "
                        "Call remove_account again with 'account' set to one of the names below."
                    ),
                    "configured_accounts": choices,
                })

            if account not in _accounts:
                return _fmt(f"Error: Unknown account '{account}'. Available: {list(_accounts.keys())}")

            removed_info = dict(_accounts[account])
            had_token = _auth.revoke_credentials(account)
            del _accounts[account]
            try:
                save_config(_config)
            except Exception as exc:
                _accounts[account] = removed_info
                raise RuntimeError(
                    f"Credentials were revoked but config could not be saved: {exc}"
                ) from exc

            return _fmt({
                "status": "removed",
                "account": account,
                "email": removed_info.get("email", ""),
                "credentials_revoked": had_token,
            })

        # ---- gmail_get_profile --------------------------------------------
        elif name == "gmail_get_profile":
            svc = _get_service(args["account"])
            return _fmt(svc.get_profile())

        # ---- gmail_search -------------------------------------------------
        elif name == "gmail_search":
            query: str = args["query"]
            max_results: int = int(args.get("max_results", 10))
            include_body: bool = bool(args.get("include_body", False))
            account: str | None = args.get("account")

            if account:
                svc = _get_service(account)
                data = svc.search_messages(query, max_results, include_body=include_body)
                data["account"] = account
                data["email"] = _accounts[account].get("email", "")
                return _fmt(data)
            else:
                all_results = []
                for acct in _accounts:
                    try:
                        svc = _get_service(acct)
                        data = svc.search_messages(query, max_results, include_body=include_body)
                        all_results.append({
                            "account": acct,
                            "email": _accounts[acct].get("email", ""),
                            **data,
                        })
                    except ValueError as exc:
                        all_results.append({
                            "account": acct,
                            "error": str(exc),
                            "messages": [],
                        })
                return _fmt(all_results)

        # ---- gmail_read_message -------------------------------------------
        elif name == "gmail_read_message":
            svc = _get_service(args["account"])
            return _fmt(svc.get_message(args["message_id"]))

        # ---- gmail_read_thread --------------------------------------------
        elif name == "gmail_read_thread":
            svc = _get_service(args["account"])
            return _fmt(svc.get_thread(args["thread_id"]))

        # ---- gmail_send ---------------------------------------------------
        elif name == "gmail_send":
            svc = _get_service(args["account"])
            result = svc.send_message(
                to=args["to"],
                subject=args["subject"],
                body=args["body"],
                cc=args.get("cc", ""),
                bcc=args.get("bcc", ""),
                thread_id=args.get("thread_id", ""),
                in_reply_to=args.get("in_reply_to", ""),
                references=args.get("references", ""),
            )
            return _fmt({
                "status": "sent",
                "message_id": result.get("id"),
                "thread_id": result.get("threadId"),
            })

        # ---- gmail_create_draft -------------------------------------------
        elif name == "gmail_create_draft":
            svc = _get_service(args["account"])
            result = svc.create_draft(
                to=args["to"],
                subject=args["subject"],
                body=args["body"],
                cc=args.get("cc", ""),
                bcc=args.get("bcc", ""),
                thread_id=args.get("thread_id", ""),
                in_reply_to=args.get("in_reply_to", ""),
                references=args.get("references", ""),
            )
            return _fmt({"status": "draft created", "draft_id": result.get("id")})

        # ---- gmail_send_draft --------------------------------------------
        elif name == "gmail_send_draft":
            svc = _get_service(args["account"])
            result = svc.send_draft(args["draft_id"])
            return _fmt({
                "status": "sent",
                "message_id": result.get("id"),
                "thread_id": result.get("threadId"),
            })

        # ---- gmail_list_drafts --------------------------------------------
        elif name == "gmail_list_drafts":
            svc = _get_service(args["account"])
            drafts = svc.list_drafts(int(args.get("max_results", 10)))
            return _fmt({"count": len(drafts), "drafts": drafts})

        # ---- gmail_list_labels --------------------------------------------
        elif name == "gmail_list_labels":
            svc = _get_service(args["account"])
            return _fmt(svc.list_labels())

        # ---- gmail_modify_labels -----------------------------------------
        elif name == "gmail_modify_labels":
            svc = _get_service(args["account"])
            svc.modify_labels(
                message_id=args["message_id"],
                add_labels=args.get("add_labels"),
                remove_labels=args.get("remove_labels"),
            )
            return _fmt({"status": "labels updated", "message_id": args["message_id"]})

        # ---- gmail_trash -------------------------------------------------
        elif name == "gmail_trash":
            svc = _get_service(args["account"])
            svc.trash_message(args["message_id"])
            return _fmt({"status": "moved to trash", "message_id": args["message_id"]})

        # ---- calendar_list_calendars --------------------------------------
        elif name == "calendar_list_calendars":
            svc = _get_calendar(args["account"])
            return _fmt(svc.list_calendars())

        # ---- calendar_list_events -----------------------------------------
        elif name == "calendar_list_events":
            svc = _get_calendar(args["account"])
            return _fmt(svc.list_events(
                time_min=args.get("time_min"),
                time_max=args.get("time_max"),
                max_results=int(args.get("max_results", 20)),
                calendar_id=args.get("calendar_id", "primary"),
            ))

        # ---- calendar_search ----------------------------------------------
        elif name == "calendar_search":
            svc = _get_calendar(args["account"])
            return _fmt(svc.search_events(
                query=args["query"],
                time_min=args.get("time_min"),
                time_max=args.get("time_max"),
                max_results=int(args.get("max_results", 20)),
                calendar_id=args.get("calendar_id", "primary"),
            ))

        # ---- calendar_get_event -------------------------------------------
        elif name == "calendar_get_event":
            svc = _get_calendar(args["account"])
            return _fmt(svc.get_event(
                event_id=args["event_id"],
                calendar_id=args.get("calendar_id", "primary"),
            ))

        # ---- calendar_create_event ----------------------------------------
        elif name == "calendar_create_event":
            source_account = args["account"]
            svc = _get_calendar(source_account)
            created = svc.create_event(
                title=args["title"],
                start_time=args["start_time"],
                end_time=args["end_time"],
                calendar_id=args.get("calendar_id", "primary"),
                description=args.get("description", ""),
                location=args.get("location", ""),
                attendees=args.get("attendees"),
            )

            # Auto-push a busy block to every other configured account
            blocks_pushed = []
            for other_account in _accounts:
                if other_account == source_account:
                    continue
                try:
                    target_svc = _get_calendar(other_account)
                    result = target_svc.sync_blocks_from(
                        source_service=svc,
                        time_min=args["start_time"],
                        time_max=args["end_time"],
                        source_calendar_id=args.get("calendar_id", "primary"),
                        target_calendar_id="primary",
                    )
                    blocks_pushed.append({
                        "target_account": other_account,
                        "blocks_created": result["blocks_created"],
                        "blocks_skipped": result["blocks_skipped"],
                    })
                except Exception as e:
                    blocks_pushed.append({"target_account": other_account, "error": str(e)})

            return _fmt({**created, "synced_blocks": blocks_pushed})

        # ---- calendar_update_event ----------------------------------------
        elif name == "calendar_update_event":
            svc = _get_calendar(args["account"])
            return _fmt(svc.update_event(
                event_id=args["event_id"],
                calendar_id=args.get("calendar_id", "primary"),
                title=args.get("title"),
                start_time=args.get("start_time"),
                end_time=args.get("end_time"),
                description=args.get("description"),
                location=args.get("location"),
                attendees=args.get("attendees"),
            ))

        # ---- calendar_delete_event ----------------------------------------
        elif name == "calendar_delete_event":
            svc = _get_calendar(args["account"])
            return _fmt(svc.delete_event(
                event_id=args["event_id"],
                calendar_id=args.get("calendar_id", "primary"),
            ))

        # ---- calendar_sync_blocks ----------------------------------------
        elif name == "calendar_sync_blocks":
            source_svc = _get_calendar(args["source_account"])
            target_svc = _get_calendar(args["target_account"])
            return _fmt(target_svc.sync_blocks_from(
                source_service=source_svc,
                time_min=args.get("time_min"),
                time_max=args.get("time_max"),
                source_calendar_id=args.get("source_calendar_id", "primary"),
                target_calendar_id=args.get("target_calendar_id", "primary"),
            ))

        # ---- drive_list_files --------------------------------------------
        elif name == "drive_list_files":
            svc = _get_drive(args["account"])
            return _fmt(svc.list_files(
                max_results=int(args.get("max_results", 20)),
                folder_id=args.get("folder_id"),
                mime_type=args.get("mime_type"),
            ))

        # ---- drive_search ------------------------------------------------
        elif name == "drive_search":
            svc = _get_drive(args["account"])
            return _fmt(svc.search_files(
                query=args["query"],
                max_results=int(args.get("max_results", 20)),
            ))

        # ---- drive_list_folders ------------------------------------------
        elif name == "drive_list_folders":
            svc = _get_drive(args["account"])
            return _fmt(svc.list_folders(max_results=int(args.get("max_results", 20))))

        # ---- drive_get_file ----------------------------------------------
        elif name == "drive_get_file":
            svc = _get_drive(args["account"])
            return _fmt(svc.get_file(args["file_id"]))

        # ---- drive_read_file ---------------------------------------------
        elif name == "drive_read_file":
            svc = _get_drive(args["account"])
            return _fmt(svc.read_file(args["file_id"]))

        # ---- drive_download_file -----------------------------------------
        elif name == "drive_download_file":
            svc = _get_drive(args["account"])
            return _fmt(svc.download_file(args["file_id"]))

        # ---- drive_delete_file -------------------------------------------
        elif name == "drive_delete_file":
            svc = _get_drive(args["account"])
            return _fmt(svc.delete_file(args["file_id"]))

        else:
            return _fmt(f"Unknown tool: {name}")

    except ValueError as exc:
        return _fmt(f"Error: {exc}")
    except Exception as exc:
        return _fmt(f"Error in '{name}': {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
