# clockify-mcp

An MCP server that gives Claude read access to your Clockify data: who you are,
your workspaces, and which projects/clients/tasks you're assigned to.

## Setup

```bash
git clone https://github.com/jack-e-hobbs/clockify-mcp.git
cd clockify-mcp
pip3 install -r requirements.txt
```

Get your personal API key from https://app.clockify.me/manage-api-keys . This is tied to your Clockify login — don't
share it, and don't commit it anywhere.

## Add to Claude desktop (Cowork)

Settings → Connectors → Add custom connector (or edit
`~/Library/Application Support/Claude/claude_desktop_config.json` directly)
and add an entry under `mcpServers`:

```json
"clockify": {
  "command": "python3",
  "args": ["/absolute/path/to/clockify-mcp/server.py"],
  "env": {
    "CLOCKIFY_API_KEY": "your-key-here"
  }
}
```

Use the full path to your `python3` (`which python3`) and to `server.py`.
Restart Claude desktop fully (quit, not just close the window) — MCP servers
only connect on launch.

## Tools

- `whoami` — your Clockify id, name, email
- `workspaces` — workspaces you belong to
- `my_tasks` — projects (with client) you're a member of, and each project's
  active tasks, across all workspaces
- `list_projects` — every project (with client and tasks) across all
  workspaces, regardless of membership; surfaces internal/non-client projects
  `my_tasks` hides
- `log_time` — log a completed time entry against a project (task optional),
  given a start and end timestamp; matched by name from `list_projects`
- `list_time_entries` — your already-logged entries, newest first, with
  optional ISO start/end bounds

## Updating

```bash
git pull
pip3 install -r requirements.txt  # only needed if requirements.txt changed
```

Then restart Claude desktop.
