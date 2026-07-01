#!/usr/bin/env python3
"""Clockify MCP server: tools for who am I, workspaces, and my assigned tasks.

Auth: CLOCKIFY_API_KEY env var (set via the MCP server's `env` config).
"""
import json
import os
import urllib.request

from mcp.server.fastmcp import FastMCP

BASE = "https://api.clockify.me/api/v1"
mcp = FastMCP("clockify")


def _get(path):
    key = os.environ["CLOCKIFY_API_KEY"]
    req = urllib.request.Request(f"{BASE}{path}", headers={"X-Api-Key": key})
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


if __name__ == "__main__":
    mcp.run()
