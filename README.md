# LinkedIn for Claude

Plan, write, schedule and publish LinkedIn posts by talking to Claude.

Claude does the writing itself, so there are no AI API keys to manage. This repo gives Claude what it can't do on its own:

- **LinkedIn tools** (an MCP server): post text and images, connect your account, check its status.
- **A content calendar**: ideas → drafts → scheduled → published, stored locally in SQLite.
- **Title-card images**, rendered locally (no image API).
- **Skills** that teach Claude your posting workflow and writing style: `/linkedin:plan` and `/linkedin:write`.

It works as a **Claude Code plugin** and as a **Claude Desktop connector**.

```
You: plan 30 days of posts about AI strategy for small businesses
Claude: (brainstorms, saves 30 dated ideas)  Here's your calendar...

You: write today's post and make an image for it
Claude: (drafts it in your style, renders a title card)  Here's the draft...

You: shorten the second paragraph, then schedule it for tomorrow 9:30
Claude: Scheduled for 2026-10-24 09:30.
```

## Setup

### 1. Install uv

The server runs with [uv](https://docs.astral.sh/uv/), which installs the Python dependencies automatically on first run.

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"   # Windows
curl -LsSf https://astral.sh/uv/install.sh | sh                                      # macOS / Linux
```

### 2. Create a LinkedIn developer app (one time)

1. Create an app at <https://www.linkedin.com/developers/apps>. It must be linked to a LinkedIn company page; any page you admin will do.
2. **Products** tab: add **Sign In with LinkedIn using OpenID Connect** and **Share on LinkedIn**.
3. **Auth** tab: add the redirect URL `http://localhost:8000/callback`.
4. Copy the Client ID and Client Secret into `~/.linkedin-mcp/.env` (`%USERPROFILE%\.linkedin-mcp\.env` on Windows):

   ```ini
   LINKEDIN_CLIENT_ID=...
   LINKEDIN_CLIENT_SECRET=...
   ```

To post as a **company page** instead of yourself, the app also needs the *Community Management API* product. Then add:

```ini
LINKEDIN_AUTHOR_URN=urn:li:organization:<page id>
LINKEDIN_SCOPES=openid profile w_member_social w_organization_social r_organization_social
```

### 3a. Claude Code: install the plugin

```
/plugin marketplace add aaryansolankii/LinkedIn-Automation
/plugin install linkedin@linkedin-automation
```

Working from a local clone instead? Use `/plugin marketplace add <path-to-this-repo>`, or start Claude with `claude --plugin-dir <path-to-this-repo>`.

### 3b. Claude Desktop: add the connector

Edit `claude_desktop_config.json`. On Windows it's at `%APPDATA%\Claude\`; on macOS at `~/Library/Application Support/Claude/`. Then fully restart Claude Desktop.

```json
{
  "mcpServers": {
    "linkedin": {
      "command": "uv",
      "args": ["run", "--directory", "C:/path/to/LinkedIn-Automation", "--quiet", "linkedin-mcp"]
    }
  }
}
```

For the writing style and workflow, zip each folder in `skills/` (e.g. `write.zip` containing `write/SKILL.md`) and upload it under **Customize → Skills**.

### 4. Connect your account

Ask Claude to *"connect my LinkedIn account"*. A browser tab opens; approve access there, and you're done. You can also run `uv run linkedin-mcp login` from this folder.

LinkedIn tokens last about 60 days. When yours expires, Claude will tell you, and you just log in again.

## Using it

| Ask Claude… | What happens |
|---|---|
| `/linkedin:plan AI in healthcare, 3 posts a week` | Ideas are brainstormed and saved to the calendar |
| "show my content calendar" | Lists ideas, drafts and scheduled posts |
| `/linkedin:write` or "write today's post" | Drafts the next idea in your style and saves it as a draft |
| "make an image for it, dark theme" | Renders a title card and attaches it |
| "schedule it for Monday 9am" / "post it now" | Schedules or publishes, only after you approve the final text |

Claude never publishes without your explicit approval of the exact text. Claude Code also asks permission before each tool call unless you allow it.

### Tools

| Tool | Purpose |
|---|---|
| `linkedin_status` | Connection, token expiry, calendar totals, current time |
| `linkedin_login` | Browser login; saves the token and your author URN |
| `plan_posts` | Add dated ideas to the calendar |
| `list_posts`, `get_post` | Read the calendar |
| `save_post` | Create or edit a post (text, notes, image, planned time) |
| `make_post_image` | Render a 1200×1200 title card (themes: sky, mint, peach, lavender, midnight) |
| `schedule_post` | Approve a draft for publishing at a set time |
| `publish_post` | Publish now |
| `publish_due` | Publish every scheduled post whose time has come |
| `delete_post` | Remove a post from the calendar |

## Automatic publishing of scheduled posts

Scheduled posts go out when `publish_due` runs. Claude runs it if you ask; to make it fully automatic, have your OS run it every 15 minutes.

**Windows** (PowerShell; replace the path with your clone):

```powershell
$action  = New-ScheduledTaskAction -Execute "$env:USERPROFILE\.local\bin\uvw.exe" `
           -Argument 'run --directory "C:\path\to\LinkedIn-Automation" --quiet linkedin-mcp publish-due'
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName "LinkedIn publish-due" -Action $action -Trigger $trigger
```

`uvw.exe` is uv's windowless launcher, so no console flashes. Remove the task with `Unregister-ScheduledTask "LinkedIn publish-due"`.

**macOS / Linux** (`crontab -e`):

```
*/15 * * * * cd /path/to/LinkedIn-Automation && uv run --quiet linkedin-mcp publish-due
```

If a scheduled post fails (for example because the token expired), it is marked `failed` with the error and never retried silently. Ask Claude to reschedule it.

## Configuration

Settings are read from the environment first, then from `~/.linkedin-mcp/.env`. See [.env.example](.env.example).

| Variable | Default | |
|---|---|---|
| `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET` | | Required for login |
| `LINKEDIN_AUTHOR_URN` | your profile | e.g. `urn:li:organization:123` to post as a page |
| `LINKEDIN_SCOPES` | `openid profile w_member_social` | Add organization scopes for page posting |
| `LINKEDIN_REDIRECT_URI` | `http://localhost:8000/callback` | Must match the app's Auth settings |
| `POSTING_TIME` | `10:00` | Time used when only a date is given |
| `LINKEDIN_API_VERSION` | `202610` | LinkedIn REST version (`YYYYMM`); versions expire after about a year |
| `LINKEDIN_ACCESS_TOKEN` | | Use an existing token instead of logging in |
| `LINKEDIN_DATA_DIR` | `~/.linkedin-mcp` | Where everything below lives |

Data directory:

```
~/.linkedin-mcp/
├── .env               your settings
├── credentials.json   token from linkedin_login
├── posts.db           the content calendar (SQLite)
├── images/            title cards
└── linkedin-mcp.log   log, including scheduled publishing runs
```

## Development

```bash
uv sync                          # create .venv
uv run linkedin-mcp status       # check the connection and calendar from the terminal
claude --plugin-dir .            # try the plugin; /reload-plugins after edits
claude plugin validate .         # check the plugin and marketplace manifests
```

Code lives in `src/linkedin_mcp/`: `server.py` (MCP tools), `linkedin.py` (API client), `auth.py` (OAuth login), `store.py` (calendar), `cards.py` (images) and `config.py` (settings).
