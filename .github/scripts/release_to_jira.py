"""Sync a published GitHub release into Jira and Confluence.

Triggered by .github/workflows/release-to-jira.yml on `release: published`.

Flow:
  1. Collect PR numbers referenced in the release notes body.
  2. Fetch each PR and extract AIPLAT-### Jira keys from its title/body.
  3. Create (or reuse) a Jira Version named after the release tag.
  4. Set fixVersion on each ticket.
  5. Mark the Version released.
  6. Publish (or update) release notes as a child page under the Release Notes Folder.

NOTE: This authenticates as an Atlassian *service account*, which must call
the tenant gateway at api.atlassian.com/ex/{product}/{cloudId}/... — NOT the
site URL (mozilla-hub.atlassian.net), which returns 404 for service accounts.
Human-facing links (issue /browse, Confluence webui) still use the site URL.

Idempotent: reuses an existing Version and only *adds* fixVersions, and the
Confluence page is upserted (updated in place if a page with the same title
already exists), so re-running for the same tag never errors or duplicates.
"""

import datetime
import html
import os
import re
import sys
import time

import requests

GH_API = "https://api.github.com"
GH_REPO = os.environ["GH_REPO"]
GH_TOKEN = os.environ["GH_TOKEN"]
TAG = os.environ["RELEASE_TAG"]
BODY = os.environ.get("RELEASE_BODY", "")
REL_URL = os.environ["RELEASE_URL"]

CLOUD_ID = os.environ["ATLASSIAN_CLOUD_ID"]  # d8febd08-...
SITE = os.environ.get("JIRA_SITE_URL", "https://mozilla-hub.atlassian.net").rstrip("/")
PROJ = os.environ["JIRA_PROJECT_KEY"]  # AIPLAT
AUTH = (os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])
PARENT_ID = os.environ["CONFLUENCE_PARENT_ID"]  # 2885845046

# Service-account gateway bases (NOT the site URL).
JIRA_API = f"https://api.atlassian.com/ex/jira/{CLOUD_ID}"
CONF_API = f"https://api.atlassian.com/ex/confluence/{CLOUD_ID}"

gh = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
KEY_RE = re.compile(rf"\b{PROJ}-\d+\b", re.IGNORECASE)


def jira(method, path, **kw):
    r = requests.request(
        method,
        f"{JIRA_API}{path}",
        auth=AUTH,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=30,
        **kw,
    )
    if not r.ok:
        print(f"{method} {path} -> {r.status_code} {r.text}", file=sys.stderr)
    r.raise_for_status()
    return r.json() if r.text else {}


def jira_retry(method, path, retries=4, delay=1.5, **kw):
    """Like jira(), but retries on 404 to absorb Jira's replication lag right
    after creating a resource (e.g. PUT to a Version immediately after POST)."""
    for attempt in range(retries):
        try:
            return jira(method, path, **kw)
        except requests.HTTPError as e:
            if e.response.status_code != 404 or attempt == retries - 1:
                raise
            print(
                f"  {method} {path} -> 404, retrying in {delay}s "
                f"({attempt + 1}/{retries})",
                file=sys.stderr,
            )
            time.sleep(delay)
            delay *= 2


def conf(method, path, **kw):
    r = requests.request(
        method,
        f"{CONF_API}{path}",
        auth=AUTH,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=30,
        **kw,
    )
    if not r.ok:
        print(f"{method} {path} -> {r.status_code} {r.text}", file=sys.stderr)
    r.raise_for_status()
    return r.json() if r.text else {}


# 1. Collect PR numbers referenced in the release notes body.
pr_numbers = sorted(set(int(n) for n in re.findall(r"/pull/(\d+)", BODY)))
print(f"PRs in release: {pr_numbers}")

# 2. Fetch each PR, extract AIPLAT-### keys from title + body.
tickets = set()
pr_lines = []
for n in pr_numbers:
    r = requests.get(f"{GH_API}/repos/{GH_REPO}/pulls/{n}", headers=gh, timeout=30)
    r.raise_for_status()
    pr = r.json()
    text = f"{pr['title']}\n{pr.get('body') or ''}"
    keys = {k.upper() for k in KEY_RE.findall(text)}
    tickets |= keys
    pr_lines.append((n, pr["title"], sorted(keys), pr["html_url"]))
tickets = sorted(tickets)
print(f"Tickets found: {tickets}")

# 3. Create (or reuse) the Jira Version.
proj = jira("GET", f"/rest/api/3/project/{PROJ}")
versions = jira("GET", f"/rest/api/3/project/{PROJ}/versions")
version = next((v for v in versions if v["name"] == TAG), None)
if not version:
    version = jira(
        "POST",
        "/rest/api/3/version",
        json={
            "name": TAG,
            "projectId": proj["id"],
            "released": False,
            "description": f"Auto-created from GitHub release {REL_URL}",
        },
    )
    print(f"Created version {TAG} ({version['id']})")
else:
    print(f"Reusing existing version {TAG} ({version['id']})")

# 4. Attach fixVersion to each ticket (skip missing / no-permission).
attached = []
for key in tickets:
    try:
        jira(
            "PUT",
            f"/rest/api/3/issue/{key}",
            json={"update": {"fixVersions": [{"add": {"name": TAG}}]}},
        )
        attached.append(key)
    except requests.HTTPError:
        print(f"  ! could not update {key} (missing/no-permission), skipping")
print(f"Attached fixVersion to: {attached}")

if version.get("released"):
    today = version.get("releaseDate") or datetime.date.today().isoformat()
    print(f"Version {TAG} already released on {today}, leaving releaseDate as-is")
else:
    today = datetime.date.today().isoformat()
    jira_retry(
        "PUT",
        f"/rest/api/3/version/{version['id']}",
        json={"released": True, "releaseDate": today},
    )
    print(f"Released version {TAG}")


# 6. Build release notes and publish as a child of the Release Notes Folder.
def li(items):
    return "".join(f"<li>{x}</li>" for x in items)


def esc(s):
    return html.escape(str(s), quote=True)


ticket_html = (
    li([f'<a href="{esc(SITE)}/browse/{esc(k)}">{esc(k)}</a>' for k in attached])
    or "<li>None linked</li>"
)
pr_html = li(
    [
        f"#{n} {esc(t)} "
        + (
            " ".join(f'<a href="{esc(SITE)}/browse/{esc(k)}">{esc(k)}</a>' for k in ks)
            or "(no ticket)"
        )
        + f' — <a href="{esc(u)}">PR</a>'
        for n, t, ks, u in pr_lines
    ]
)
page_html = (
    f"<h2>{esc(TAG)}</h2>"
    f'<p>Released {esc(today)} · <a href="{esc(REL_URL)}">GitHub release</a></p>'
    f"<h3>Jira tickets</h3><ul>{ticket_html}</ul>"
    f"<h3>Pull requests</h3><ul>{pr_html}</ul>"
)

# Parent page tells us which space to create in (via the service-account gateway).
space_id = conf("GET", f"/wiki/api/v2/pages/{PARENT_ID}")["spaceId"]
title = f"MLPA Release {TAG}"

# Upsert: update the page in place if one with this title already exists in the
# space, otherwise create it. Confluence titles are unique per space, so a plain
# create would 400 on any re-run.
found = conf(
    "GET",
    "/wiki/api/v2/pages",
    params={"space-id": space_id, "title": title, "status": "current", "limit": 1},
)
existing = (found.get("results") or [None])[0]

if existing:
    page_id = existing["id"]
    current_ver = conf("GET", f"/wiki/api/v2/pages/{page_id}")["version"]["number"]
    page = conf(
        "PUT",
        f"/wiki/api/v2/pages/{page_id}",
        json={
            "id": page_id,
            "status": "current",
            "title": title,
            "parentId": PARENT_ID,
            "body": {"representation": "storage", "value": page_html},
            "version": {
                "number": current_ver + 1,
                "message": f"Release sync for {TAG}",
            },
        },
    )
    print(f"Updated existing page {page_id} -> v{current_ver + 1}")
else:
    page = conf(
        "POST",
        "/wiki/api/v2/pages",
        json={
            "spaceId": space_id,
            "status": "current",
            "parentId": PARENT_ID,
            "title": title,
            "body": {"representation": "storage", "value": page_html},
        },
    )
    print(f"Created page {page['id']}")

page_url = f"{SITE}/wiki{page['_links']['webui']}"

# Store the release-notes URL on the Jira version so a "Version released"
# automation can surface it (e.g. link it in Slack) via {{version.description}}.
jira_retry("PUT", f"/rest/api/3/version/{version['id']}", json={"description": page_url})

print(f"Confluence page: {page_url}")

# Expose values to later workflow steps (the Slack notification).
gh_out = os.environ.get("GITHUB_OUTPUT")
if gh_out:
    with open(gh_out, "a") as f:
        f.write(f"tag={TAG}\n")
        f.write(f"page_url={page_url}\n")
