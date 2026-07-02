#!/usr/bin/env python3
"""Clockify MCP server: tools for who am I, workspaces, and my assigned tasks.

Auth: CLOCKIFY_API_KEY env var (set via the MCP server's `env` config).
"""
import json
import os
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP

BASE = "https://api.clockify.me/api/v1"
mcp = FastMCP("clockify")


def _get(path):
    key = os.environ["CLOCKIFY_API_KEY"]
    req = urllib.request.Request(f"{BASE}{path}", headers={"X-Api-Key": key})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def _post(path, body):
    key = os.environ["CLOCKIFY_API_KEY"]
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method="POST",
        headers={"X-Api-Key": key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


@mcp.tool()
def whoami() -> dict:
    """Return the authenticated Clockify user's name, email, and id."""
    u = _get("/user")
    return {"id": u["id"], "name": u["name"], "email": u["email"]}


@mcp.tool()
def workspaces() -> list[dict]:
    """List workspaces the authenticated user belongs to."""
    return [{"id": w["id"], "name": w["name"]} for w in _get("/workspaces")]


@mcp.tool()
def my_tasks() -> list[dict]:
    """List projects (with client) the authenticated user is a member of, and each project's tasks, across all workspaces.

    Note: this workspace assigns work at the project-membership level, not
    per-task, so "my tasks" means tasks on projects I'm a member of.
    """
    user_id = _get("/user")["id"]
    result = []
    for ws in _get("/workspaces"):
        for p in _get(f"/workspaces/{ws['id']}/projects?page-size=200"):
            member_ids = {m["userId"] for m in p.get("memberships", [])}
            if user_id not in member_ids:
                continue
            tasks = _get(f"/workspaces/{ws['id']}/projects/{p['id']}/tasks?page-size=200")
            result.append({
                "workspace": ws["name"],
                "project": p["name"],
                "client": p.get("clientName") or None,
                "tasks": [t["name"] for t in tasks if t["status"] == "ACTIVE"],
            })
    return result


@mcp.tool()
def list_projects() -> list[dict]:
    """List every project (with client and tasks) in each workspace, regardless of membership.

    Unlike my_tasks(), this includes internal/non-client projects and projects
    with no tasks — use it to find loggable targets my_tasks() doesn't surface.
    """
    result = []
    for ws in _get("/workspaces"):
        for p in _get(f"/workspaces/{ws['id']}/projects?page-size=200"):
            tasks = _get(f"/workspaces/{ws['id']}/projects/{p['id']}/tasks?page-size=200")
            result.append({
                "workspace": ws["name"],
                "project": p["name"],
                "client": p.get("clientName") or None,
                "tasks": [t["name"] for t in tasks if t["status"] == "ACTIVE"],
            })
    return result


@mcp.tool()
def log_time(project: str, start: str, end: str, task: str = "", description: str = "") -> dict:
    """Log a fixed (already-completed) time entry against a project (and optional task).

    project/task match case-insensitively by substring against list_projects()
    results — use exact names to avoid ambiguity. Leave task empty for projects
    with no Clockify tasks (e.g. internal/non-billable projects).
    start/end are ISO-8601 UTC timestamps, e.g. "2026-07-01T09:00:00Z".
    Billable/non-billable is inherited from the project's own setting.
    """
    for ws in _get("/workspaces"):
        for p in _get(f"/workspaces/{ws['id']}/projects?page-size=200"):
            if project.lower() not in p["name"].lower():
                continue
            body = {"start": start, "end": end, "projectId": p["id"], "description": description}
            matched_task = None
            if task:
                for t in _get(f"/workspaces/{ws['id']}/projects/{p['id']}/tasks?page-size=200"):
                    if task.lower() in t["name"].lower():
                        body["taskId"] = t["id"]
                        matched_task = t["name"]
                        break
                if matched_task is None:
                    raise ValueError(f"Project {p['name']!r} matched but no task matches {task!r} — check list_projects()")
            entry = _post(f"/workspaces/{ws['id']}/time-entries", body)
            return {"logged": True, "project": p["name"], "task": matched_task, "id": entry["id"]}
    raise ValueError(f"No project match for project={project!r} — check list_projects() for exact names")


@mcp.tool()
def list_time_entries(start: str = "", end: str = "", page_size: int = 50) -> list[dict]:
    """List my already-logged time entries across all workspaces, newest first.

    start/end are optional ISO-8601 UTC bounds, e.g. "2026-07-01T00:00:00Z";
    omit for the most recent entries. page_size caps results per workspace.
    """
    user_id = _get("/user")["id"]
    result = []
    for ws in _get("/workspaces"):
        # cache project id->name so we can label entries without extra lookups
        projects = {p["id"]: p for p in _get(f"/workspaces/{ws['id']}/projects?page-size=200")}
        params = {"page-size": page_size}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        qs = urllib.parse.urlencode(params)
        for e in _get(f"/workspaces/{ws['id']}/user/{user_id}/time-entries?{qs}"):
            p = projects.get(e.get("projectId"))
            result.append({
                "workspace": ws["name"],
                "project": p["name"] if p else None,
                "client": (p.get("clientName") or None) if p else None,
                "description": e.get("description"),
                "start": e["timeInterval"]["start"],
                "end": e["timeInterval"]["end"],
                "duration": e["timeInterval"]["duration"],
                "id": e["id"],
            })
    return result


if __name__ == "__main__":
    mcp.run()
