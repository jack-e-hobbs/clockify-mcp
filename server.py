#!/usr/bin/env python3
"""Clockify MCP server: tools for who am I, workspaces, and my assigned tasks.

Auth: locally (stdio) via CLOCKIFY_API_KEY env var. When hosted (streamable-http),
each caller instead sends their own key via the X-Clockify-Key request header.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.fastmcp import Context, FastMCP

BASE = "https://api.clockify.me/api/v1"
REPORTS_BASE = "https://reports.api.clockify.me/v1"  # reports live on a separate host
mcp = FastMCP("clockify", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), stateless_http=True)


def _api_key(ctx: Context) -> str:
    request = ctx.request_context.request
    if request is not None:
        key = request.headers.get("X-Clockify-Key")
        if key:
            return key
    return os.environ["CLOCKIFY_API_KEY"]


def _get(ctx, path):
    req = urllib.request.Request(f"{BASE}{path}", headers={"X-Api-Key": _api_key(ctx)})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def _post(ctx, path, body):
    return _write(ctx, path, "POST", body)


def _put(ctx, path, body):
    return _write(ctx, path, "PUT", body)


def _write(ctx, path, method, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"X-Api-Key": _api_key(ctx), "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def _report_post(ctx, path, body):
    """POST to the Reports API (separate host, paid plans only)."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{REPORTS_BASE}{path}",
        data=data,
        method="POST",
        headers={"X-Api-Key": _api_key(ctx), "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def _delete(ctx, path):
    """DELETE; returns True, or False if the resource was already gone (404)."""
    req = urllib.request.Request(f"{BASE}{path}", method="DELETE", headers={"X-Api-Key": _api_key(ctx)})
    try:
        with urllib.request.urlopen(req):
            return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        if e.code == 403:
            raise ValueError("Your API key can't delete this entry — you likely don't own it and lack admin rights")
        raise


def _resolve_tag_ids(ctx, ws_id, tag):
    """Return [tagId] for an existing tag name. Tags must be pre-created in Clockify
    (creating them needs workspace-admin rights the API key may not have)."""
    for t in _get(ctx, f"/workspaces/{ws_id}/tags?page-size=200"):
        if t["name"].lower() == tag.lower():
            return [t["id"]]
    raise ValueError(f"No tag named {tag!r} in this workspace — create it once in Clockify (Settings → Tags) first")


def _find_entry(ctx, entry_id):
    """Locate a time entry by id across workspaces. Returns (ws_id, entry) or (None, None)."""
    for ws in _get(ctx, "/workspaces"):
        try:
            e = _get(ctx, f"/workspaces/{ws['id']}/time-entries/{entry_id}")
            return ws["id"], e
        except urllib.error.HTTPError as err:
            # 404/403 = not in this workspace; 400 = Clockify's response for a deleted/invalid id
            if err.code in (400, 403, 404):
                continue
            raise
    return None, None


@mcp.tool()
def whoami(ctx: Context) -> dict:
    """Return the authenticated Clockify user's name, email, and id."""
    u = _get(ctx, "/user")
    return {"id": u["id"], "name": u["name"], "email": u["email"]}


@mcp.tool()
def workspaces(ctx: Context) -> list[dict]:
    """List workspaces the authenticated user belongs to."""
    return [{"id": w["id"], "name": w["name"]} for w in _get(ctx, "/workspaces")]


@mcp.tool()
def my_tasks(ctx: Context) -> list[dict]:
    """List projects (with client) the authenticated user is a member of, and each project's tasks, across all workspaces.

    Note: this workspace assigns work at the project-membership level, not
    per-task, so "my tasks" means tasks on projects I'm a member of.
    """
    user_id = _get(ctx, "/user")["id"]
    result = []
    for ws in _get(ctx, "/workspaces"):
        for p in _get(ctx, f"/workspaces/{ws['id']}/projects?page-size=200"):
            member_ids = {m["userId"] for m in p.get("memberships", [])}
            if user_id not in member_ids:
                continue
            tasks = _get(ctx, f"/workspaces/{ws['id']}/projects/{p['id']}/tasks?page-size=200")
            result.append({
                "workspace": ws["name"],
                "project": p["name"],
                "client": p.get("clientName") or None,
                "tasks": [t["name"] for t in tasks if t["status"] == "ACTIVE"],
            })
    return result


@mcp.tool()
def list_projects(ctx: Context) -> list[dict]:
    """List every project (with client and tasks) in each workspace, regardless of membership.

    Unlike my_tasks(), this includes internal/non-client projects and projects
    with no tasks — use it to find loggable targets my_tasks() doesn't surface.
    """
    result = []
    for ws in _get(ctx, "/workspaces"):
        for p in _get(ctx, f"/workspaces/{ws['id']}/projects?page-size=200"):
            tasks = _get(ctx, f"/workspaces/{ws['id']}/projects/{p['id']}/tasks?page-size=200")
            result.append({
                "workspace": ws["name"],
                "project": p["name"],
                "client": p.get("clientName") or None,
                "tasks": [t["name"] for t in tasks if t["status"] == "ACTIVE"],
            })
    return result


@mcp.tool()
def log_time(ctx: Context, project: str, start: str, end: str, task: str = "", description: str = "",
             tag: str = "", billable: bool | None = None) -> dict:
    """Log a fixed (already-completed) time entry against a project (and optional task).

    project/task match case-insensitively by substring against list_projects()
    results — use exact names to avoid ambiguity. Leave task empty for projects
    with no Clockify tasks (e.g. internal/non-billable projects).

    TIMEZONE GOTCHA: start/end LOOK like ISO-8601 UTC (e.g. "2026-07-01T09:00:00Z")
    but Clockify treats the value as literal wall-clock time — it does NOT apply a
    UTC offset. A 9am Sydney meeting must be sent as "...T09:00:00Z", not shifted.
    Getting this wrong logs the entry at the wrong hour (and sometimes wrong day).

    tag: optional name of an existing tag to attach — e.g. "source:outlook". Create
         the tag once in Clockify first (the API key can't create tags).
    billable: override the project's default billable setting; omit to inherit.
    Returns the new entry id — keep it to later update_time_entry / delete_time_entry.
    """
    for ws in _get(ctx, "/workspaces"):
        for p in _get(ctx, f"/workspaces/{ws['id']}/projects?page-size=200"):
            if project.lower() not in p["name"].lower():
                continue
            body = {"start": start, "end": end, "projectId": p["id"], "description": description}
            if billable is not None:
                body["billable"] = billable
            if tag:
                body["tagIds"] = _resolve_tag_ids(ctx, ws["id"], tag)
            matched_task = None
            if task:
                for t in _get(ctx, f"/workspaces/{ws['id']}/projects/{p['id']}/tasks?page-size=200"):
                    if task.lower() in t["name"].lower():
                        body["taskId"] = t["id"]
                        matched_task = t["name"]
                        break
                if matched_task is None:
                    raise ValueError(f"Project {p['name']!r} matched but no task matches {task!r} — check list_projects()")
            entry = _post(ctx, f"/workspaces/{ws['id']}/time-entries", body)
            return {"logged": True, "project": p["name"], "task": matched_task, "id": entry["id"]}
    raise ValueError(f"No project match for project={project!r} — check list_projects() for exact names")


@mcp.tool()
def list_time_entries(ctx: Context, start: str = "", end: str = "", page_size: int = 50) -> list[dict]:
    """List my already-logged time entries across all workspaces, newest first.

    start/end are optional ISO-8601 UTC bounds, e.g. "2026-07-01T00:00:00Z";
    omit for the most recent entries. page_size caps results per workspace.
    """
    user_id = _get(ctx, "/user")["id"]
    result = []
    for ws in _get(ctx, "/workspaces"):
        # cache project id->name so we can label entries without extra lookups
        projects = {p["id"]: p for p in _get(ctx, f"/workspaces/{ws['id']}/projects?page-size=200")}
        params = {"page-size": page_size}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        qs = urllib.parse.urlencode(params)
        for e in _get(ctx, f"/workspaces/{ws['id']}/user/{user_id}/time-entries?{qs}"):
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


@mcp.tool()
def update_time_entry(ctx: Context, entry_id: str, start: str = "", end: str = "", description: str = "",
                      project: str = "", task: str = "", billable: bool | None = None) -> dict:
    """Update an existing time entry (e.g. a meeting was rescheduled or renamed).

    Only the fields you pass are changed; everything else is preserved. Look up
    entry_id via list_time_entries() or the id returned by log_time().

    start/end carry the same TIMEZONE GOTCHA as log_time(): send literal wall-clock
    time as a "...Z" string, do NOT apply a UTC offset.

    project/task match case-insensitively by substring (like log_time). Changing
    project clears the old task unless you also pass a matching task.
    """
    ws_id, e = _find_entry(ctx, entry_id)
    if e is None:
        raise ValueError(f"No time entry found with id={entry_id!r} — check list_time_entries()")

    # PUT replaces the entry, so start from current values and override.
    body = {
        "start": start or e["timeInterval"]["start"],
        "end": end or e["timeInterval"]["end"],
        "description": description if description else e.get("description", ""),
        "billable": e.get("billable", False) if billable is None else billable,
        "projectId": e.get("projectId"),
        "taskId": e.get("taskId"),
        "tagIds": e.get("tagIds", []),
    }
    if project:
        proj = next((p for p in _get(ctx, f"/workspaces/{ws_id}/projects?page-size=200")
                     if project.lower() in p["name"].lower()), None)
        if proj is None:
            raise ValueError(f"No project match for project={project!r} — check list_projects()")
        body["projectId"] = proj["id"]
        body["taskId"] = None  # project changed → drop stale task unless re-matched below
    if task:
        pid = body["projectId"]
        match = next((t for t in _get(ctx, f"/workspaces/{ws_id}/projects/{pid}/tasks?page-size=200")
                      if task.lower() in t["name"].lower()), None)
        if match is None:
            raise ValueError(f"No task matches {task!r} on the selected project — check list_projects()")
        body["taskId"] = match["id"]

    # ISO-8601 "…Z" strings compare correctly lexicographically. A reschedule should
    # move start AND end together; passing only one can invert the interval.
    if body["end"] and body["start"] >= body["end"]:
        raise ValueError(
            f"start {body['start']} is not before end {body['end']} — pass both start and end when rescheduling")

    try:
        entry = _put(ctx, f"/workspaces/{ws_id}/time-entries/{entry_id}", body)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise ValueError("Your API key can't edit this entry — you likely don't own it and lack admin rights")
        raise
    return {"updated": True, "id": entry["id"], "start": entry["timeInterval"]["start"],
            "end": entry["timeInterval"]["end"], "description": entry.get("description")}


@mcp.tool()
def delete_time_entry(ctx: Context, entry_id: str) -> dict:
    """Delete a time entry (e.g. its meeting was cancelled).

    Idempotent: if the entry is already gone, returns deleted=False rather than erroring.
    Look up entry_id via list_time_entries() or the id returned by log_time().
    """
    ws_id, e = _find_entry(ctx, entry_id)
    if e is None:
        return {"deleted": False, "reason": "not found (already deleted?)"}
    ok = _delete(ctx, f"/workspaces/{ws_id}/time-entries/{entry_id}")
    return {"deleted": ok}


@mcp.tool()
def list_users(ctx: Context, workspace: str = "") -> list[dict]:
    """List all users in the workspace (admin view) — id, name, email, status.

    Use the ids/names to filter summary_report(). workspace: optional name substring
    to pick one workspace; omit to list users across all workspaces the key can see.
    Requires the API key to have admin/manager rights in the workspace.
    """
    result = []
    for ws in _get(ctx, "/workspaces"):
        if workspace and workspace.lower() not in ws["name"].lower():
            continue
        try:
            users = _get(ctx, f"/workspaces/{ws['id']}/users?page-size=200")
        except urllib.error.HTTPError as e:
            if e.code == 403:
                raise ValueError(f"Your API key lacks admin/manager rights in {ws['name']!r} — list_users needs that to see other members")
            raise
        for u in users:
            result.append({
                "workspace": ws["name"],
                "id": u["id"],
                "name": u["name"],
                "email": u.get("email"),
                "status": u.get("status"),
            })
    return result


@mcp.tool()
def summary_report(ctx: Context, start: str, end: str, group_by: str = "PROJECT", workspace: str = "") -> list[dict]:
    """Company-wide summary report: tracked hours, billable split, and billed amount,
    grouped by PROJECT (default), USER, CLIENT, or TASK. Covers ALL users (admin view),
    not just the caller. Requires a paid plan (Standard+) and admin/manager rights.

    start/end are date-time bounds, e.g. "2026-06-01T00:00:00.000Z" (inclusive start,
    exclusive end). Amounts are $0 until hourly rates are set on projects/members.
    Returns one entry per workspace, each with overall totals + per-group breakdown.
    """
    group_by = group_by.upper()

    def hrs(seconds):
        return round((seconds or 0) / 3600, 2)

    result = []
    for ws in _get(ctx, "/workspaces"):
        if workspace and workspace.lower() not in ws["name"].lower():
            continue

        def run(amount_shown):
            return _report_post(ctx, f"/workspaces/{ws['id']}/reports/summary", {
                "dateRangeStart": start,
                "dateRangeEnd": end,
                "summaryFilter": {"groups": [group_by]},
                "amountShown": amount_shown,
            })

        amounts_available = True
        try:
            r = run("EARNED")
        except urllib.error.HTTPError as e:
            if e.code != 403:
                raise
            # 403 on EARNED = key lacks "view amounts" rights; fall back to hours only
            amounts_available = False
            r = run("HIDE_AMOUNT")

        t = (r.get("totals") or [{}])[0]
        total_h = hrs(t.get("totalTime"))

        # Flag the common "rates not configured yet" case, and don't report a misleading
        # 0% billability when amounts are hidden (Clockify zeroes billable time then).
        warning = None
        if not amounts_available:
            billable_h = None
            amount = None
            warning = ("Amounts hidden — the API key lacks 'view rates/amounts' rights, so billable "
                       "hours and $ can't be reported. Give the key's user amount-view rights "
                       "(admin) to unlock billability. Hours tracked are accurate.")
        else:
            billable_h = hrs(t.get("totalBillableTime"))
            amount = t.get("totalAmount")
            if billable_h > 0 and not amount:
                warning = ("Billable hours tracked but $0 billed — hourly rates are likely not set "
                           "on projects/members in Clockify. Set rates to value billable time.")

        entry = {
            "workspace": ws["name"],
            "group_by": group_by,
            "total_hours": total_h,
            "billable_hours": billable_h,
            "billability_pct": (round(billable_h / total_h * 100, 1) if total_h else 0)
                               if billable_h is not None else None,
            "billed_amount": amount,
            "entries": t.get("entriesCount"),
            "breakdown": [
                {
                    "name": g.get("name"),
                    "hours": hrs(g.get("duration")),
                    "amount": g.get("amount") if amounts_available else None,
                }
                for g in (r.get("groupOne") or [])
            ],
        }
        if warning:
            entry["rates_warning"] = warning
        result.append(entry)
    return result


if __name__ == "__main__":
    mcp.run(transport=os.environ.get("MCP_TRANSPORT", "stdio"))
