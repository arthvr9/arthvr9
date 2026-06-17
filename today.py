#!/usr/bin/env python3
"""
Fetches GitHub stats via the GraphQL API and rewrites dark_mode.svg / light_mode.svg.

Stats computed:
  - Account uptime (years/months/days) from BIRTHDAY env var, or account creation date.
  - Public repositories owned, repositories contributed to.
  - Total commits authored on the default branch of owned repos.
  - Total stars across owned repos.
  - Followers.
  - Total lines of code added / removed (cached per-repo to stay under rate limits).

Env vars:
  ACCESS_TOKEN  (required) - a GitHub personal access token with `repo` + `read:user` scope.
  BIRTHDAY      (optional) - YYYY-MM-DD; falls back to the account creation date.
"""

import os
import re
import sys
import json
import datetime

import requests
from dateutil import relativedelta

API = "https://api.github.com/graphql"
TOKEN = os.environ.get("ACCESS_TOKEN")
if not TOKEN:
    sys.exit("ERROR: ACCESS_TOKEN environment variable is not set.")
HEADERS = {"Authorization": f"token {TOKEN}"}
CACHE_FILE = os.path.join("cache", "loc_cache.json")


def gql(query, variables, attempt=1):
    """Run a GraphQL query, retrying once on a transient/rate-limit error."""
    resp = requests.post(API, json={"query": query, "variables": variables}, headers=HEADERS)
    if resp.status_code == 200:
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"GraphQL errors: {data['errors']}")
        return data["data"]
    if resp.status_code in (502, 403) and attempt <= 3:
        return gql(query, variables, attempt + 1)
    raise RuntimeError(f"Query failed ({resp.status_code}): {resp.text}")


def get_viewer():
    data = gql("query { viewer { login id createdAt followers { totalCount } } }", {})
    v = data["viewer"]
    created = datetime.datetime.fromisoformat(v["createdAt"].replace("Z", "+00:00")).date()
    return v["login"], v["id"], created, v["followers"]["totalCount"]


def get_repos(login, node_id):
    """Return (total_repos, total_stars, total_commits, [(owner, name, commits), ...])."""
    query = """
    query($login: String!, $id: ID!, $cursor: String) {
      user(login: $login) {
        repositories(first: 100, after: $cursor, ownerAffiliations: [OWNER],
                     isFork: false, orderBy: {field: CREATED_AT, direction: ASC}) {
          totalCount
          pageInfo { hasNextPage endCursor }
          nodes {
            nameWithOwner
            stargazerCount
            defaultBranchRef {
              target { ... on Commit { history(author: {id: $id}) { totalCount } } }
            }
          }
        }
      }
    }"""
    total_repos = total_stars = total_commits = 0
    repos = []
    cursor = None
    while True:
        page = gql(query, {"login": login, "id": node_id, "cursor": cursor})["user"]["repositories"]
        total_repos = page["totalCount"]
        for node in page["nodes"]:
            total_stars += node["stargazerCount"]
            commits = 0
            branch = node.get("defaultBranchRef")
            if branch and branch.get("target"):
                commits = branch["target"]["history"]["totalCount"]
            total_commits += commits
            owner, name = node["nameWithOwner"].split("/", 1)
            repos.append((owner, name, commits))
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return total_repos, total_stars, total_commits, repos


def get_total_commits(login, created):
    """Sum commit contributions year-by-year (matches GitHub's own count,
    including private and organization commits when using your own token)."""
    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
          restrictedContributionsCount
        }
      }
    }"""
    total = 0
    start = datetime.datetime(created.year, created.month, created.day, tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    cursor = start
    while cursor < now:
        nxt = min(cursor + datetime.timedelta(days=365), now)
        coll = gql(query, {"login": login, "from": cursor.isoformat(), "to": nxt.isoformat()})
        coll = coll["user"]["contributionsCollection"]
        total += coll["totalCommitContributions"] + coll["restrictedContributionsCount"]
        cursor = nxt
    return total


def get_contributed(login):
    query = """
    query($login: String!) {
      user(login: $login) {
        repositoriesContributedTo(first: 1, includeUserRepositories: false,
          contributionTypes: [COMMIT, PULL_REQUEST, REPOSITORY, PULL_REQUEST_REVIEW]) {
          totalCount
        }   
      }
    }"""
    return gql(query, {"login": login})["user"]["repositoriesContributedTo"]["totalCount"]


def repo_loc(owner, name, node_id):
    """Paginate a repo's commit history (authored by the user) summing additions/deletions."""
    query = """
    query($owner: String!, $name: String!, $id: ID!, $cursor: String) {
      repository(owner: $owner, name: $name) {
        defaultBranchRef {
          target { ... on Commit {
            history(author: {id: $id}, first: 100, after: $cursor) {
              totalCount
              pageInfo { hasNextPage endCursor }
              nodes { additions deletions }
            }
          }}
        }
      }
    }"""
    additions = deletions = 0
    cursor = None
    while True:
        repo = gql(query, {"owner": owner, "name": name, "id": node_id, "cursor": cursor})["repository"]
        branch = repo.get("defaultBranchRef")
        if not branch or not branch.get("target"):
            break
        history = branch["target"]["history"]
        for c in history["nodes"]:
            additions += c["additions"]
            deletions += c["deletions"]
        if not history["pageInfo"]["hasNextPage"]:
            break
        cursor = history["pageInfo"]["endCursor"]
    return additions, deletions


def get_loc(repos, node_id):
    """Total additions/deletions across all owned repos, cached by repo + commit count."""
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cache = {}

    add_total = del_total = 0
    for owner, name, commits in repos:
        key = f"{owner}/{name}"
        entry = cache.get(key)
        if entry and entry.get("commits") == commits:
            additions, deletions = entry["additions"], entry["deletions"]
        else:
            additions, deletions = repo_loc(owner, name, node_id)
            cache[key] = {"commits": commits, "additions": additions, "deletions": deletions}
        add_total += additions
        del_total += deletions

    os.makedirs("cache", exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)
    return add_total, del_total


def uptime(birthday):
    diff = relativedelta.relativedelta(datetime.date.today(), birthday)
    parts = []
    for value, unit in ((diff.years, "year"), (diff.months, "month"), (diff.days, "day")):
        if value:
            parts.append(f"{value} {unit}" + ("s" if value != 1 else ""))
    return ", ".join(parts) or "0 days"


def set_value(svg, key, value):
    pattern = re.compile(rf"(<!--{key}-->).*?(<!--/{key}-->)", re.DOTALL)
    return pattern.sub(lambda m: m.group(1) + str(value) + m.group(2), svg)


def write_svg(path, values):
    with open(path, encoding="utf-8") as f:
        svg = f.read()
    for key, value in values.items():
        svg = set_value(svg, key, value)
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)


def main():
    login, node_id, created, followers = get_viewer()
    birthday = created
    if os.environ.get("BIRTHDAY"):
        birthday = datetime.date.fromisoformat(os.environ["BIRTHDAY"])

    total_repos, total_stars, _, repos = get_repos(login, node_id)
    total_commits = get_total_commits(login, created)
    contributed = get_contributed(login)
    add_total, del_total = get_loc(repos, node_id)

    values = {
        "uptime": uptime(birthday),
        "repos": f"{total_repos:,}",
        "contributed": f"{contributed:,}",
        "commits": f"{total_commits:,}",
        "stars": f"{total_stars:,}",
        "followers": f"{followers:,}",
        "loc": f"{add_total + del_total:,}",
        "loc_add": f"{add_total:,}",
        "loc_del": f"{del_total:,}",
    }

    for path in ("dark_mode.svg", "light_mode.svg"):
        write_svg(path, values)

    print("Updated SVGs for", login)
    for k, v in values.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
