#!/usr/bin/env python3
"""Render a neofetch GitHub profile using only Python's standard library.

Public scope is explicit even with a local PAT. ACCESS_TOKEN opts into private
repositories and private contribution aggregates; never commit that token.
LoC counts this user's additions/deletions on current default branches, including
owned forks, NOT the current tree size. API failures abort rather than publish
silently incomplete statistics. See MAINTENANCE.md for all metric definitions.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import random
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOGIN = "shiheng2016"
ROOT = Path(__file__).resolve().parent
ART = r"""
                     .
                   .-.
                 .........
                ..........:.
               .:..........:
               .:..::::::....
               .......::.....
                ......::::..
                 .....:::::
                  ....:::-.
                  ....:::..
              .:::.........-:.
         .:-=++++=  ......:-:::::..
      .-+++++++++- ....  :=------:::..
      =++++++++++- . ... -=--- :--:..
     .+++++++++++-.     .====..--.     ..
     :+++++++++++-. .  .-====-=:   ......
     =+++++++++++-...  .====-=-    . . ..
     +++==++=++++=..   :==++==.       ...
    .++++:+++++++=:.   =+++++=.  ..........
    :++++:++++++++:.  .++++++=.  ..........
"""
PALETTES = {
    "dark": dict(bg="#0d1117", border="#30363d", art="#8b949e", h="#58a6ff",
                 k="#ffa657", v="#c9d1d9", d="#484f58", g="#3fb950", r="#f85149"),
    "light": dict(bg="#ffffff", border="#d0d7de", art="#57606a", h="#0969da",
                  k="#953800", v="#24292f", d="#afb8c1", g="#1a7f37", r="#cf222e"),
}


def include_private():
    return bool(os.environ.get("ACCESS_TOKEN"))


def repo_scope():
    return "" if include_private() else ", privacy: PUBLIC"


def api(path, payload=None):
    token = os.environ.get("ACCESS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("Set GITHUB_TOKEN (or ACCESS_TOKEN to explicitly include private data).")
    url = "https://api.github.com/" + path.lstrip("/")
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": "Bearer " + token, "User-Agent": LOGIN + "-profile",
               "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(6):
        try:
            with urlopen(Request(url, data=body, headers=headers), timeout=60) as response:
                result = json.load(response)
            if isinstance(result, dict) and result.get("errors"):
                messages = "; ".join(e.get("message", "GraphQL error") for e in result["errors"])
                # Never accept GraphQL partial data as a successful measurement.
                raise RuntimeError(messages)
            return result
        except HTTPError as exc:
            retryable = exc.code in (429, 500, 502, 503, 504) or (
                exc.code == 403 and (exc.headers.get("Retry-After") or
                                    exc.headers.get("X-RateLimit-Remaining") == "0"))
            if not retryable or attempt == 5:
                raise RuntimeError(f"GitHub HTTP {exc.code} at {path}") from exc
            wait = max(2 ** attempt, int(exc.headers.get("Retry-After", "0")))
            if wait > 120:
                raise RuntimeError("GitHub rate limit requires a later retry; keeping previous SVGs.") from exc
        except (URLError, TimeoutError, ConnectionError) as exc:
            if attempt == 5:
                raise RuntimeError(f"GitHub request failed at {path}: {exc}") from exc
            wait = 2 ** attempt
        time.sleep(wait + random.random())
    raise AssertionError("unreachable")


def graphql(query, **variables):
    return api("graphql", {"query": query, "variables": variables})["data"]


def repositories():
    query = '''query($login:String!, $cursor:String) {
      user(login:$login) {
        id
        repositories(first:100, after:$cursor, ownerAffiliations:OWNER%s) {
          nodes {nameWithOwner isPrivate isFork stargazerCount
                 languages(first:3,orderBy:{field:SIZE,direction:DESC}){nodes{name}}}
          pageInfo {hasNextPage endCursor}
        }
        repositoriesContributedTo(first:1, contributionTypes:[COMMIT,PULL_REQUEST,REPOSITORY]%s){totalCount}
      }
    }''' % (repo_scope(), repo_scope())
    result, cursor, user_id, contributed = [], None, None, 0
    while True:
        user = graphql(query, login=LOGIN, cursor=cursor)["user"]
        if user is None:
            raise RuntimeError(f"GitHub user {LOGIN} not found")
        user_id = user["id"]
        contributed = user["repositoriesContributedTo"]["totalCount"]
        result.extend(user["repositories"]["nodes"])
        page = user["repositories"]["pageInfo"]
        if not page["hasNextPage"]:
            return user_id, contributed, result
        cursor = page["endCursor"]


def yearly_commits(year, now):
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = min(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc), now)
    query = '''query($login:String!, $from:DateTime!, $to:DateTime!) {
      user(login:$login) {contributionsCollection(from:$from,to:$to) {
        totalCommitContributions restrictedContributionsCount
        commitContributionsByRepository(maxRepositories:100) {
          repository {isPrivate} contributions {totalCount}
        }
      }}
    }'''
    collection = graphql(query, login=LOGIN, **{
        "from": start.isoformat(), "to": end.isoformat()})["user"]["contributionsCollection"]
    if include_private():
        return collection["totalCommitContributions"] + collection["restrictedContributionsCount"]
    rows = collection["commitContributionsByRepository"]
    if len(rows) >= 100:
        raise RuntimeError(f"{year}: repository contribution list may be truncated; refusing partial totals.")
    # A PAT may see private repositories even when ACCESS_TOKEN was not selected.
    # Filter them explicitly so local and Actions public totals have identical scope.
    return sum(row["contributions"]["totalCount"] for row in rows if not row["repository"]["isPrivate"])


def repository_lines(repo, user_id):
    owner, name = repo["nameWithOwner"].split("/", 1)
    cursor, additions, deletions, commits = None, 0, 0, 0
    query = '''query($owner:String!, $name:String!, $author:ID!, $cursor:String) {
      repository(owner:$owner,name:$name) {
        defaultBranchRef {target {... on Commit {
          history(first:100, after:$cursor, author:{id:$author}) {
            nodes {oid additions deletions}
            pageInfo {hasNextPage endCursor}
          }
        }}}
      }
    }'''
    while True:
        obj = graphql(query, owner=owner, name=name, author=user_id, cursor=cursor)["repository"]
        if obj is None:
            raise RuntimeError(f"Repository disappeared or is inaccessible: {owner}/{name}")
        ref = obj["defaultBranchRef"]
        if ref is None:  # Empty repository is a measured zero.
            return additions, deletions, commits
        if "history" not in ref["target"]:
            raise RuntimeError(f"Unsupported default-branch object: {owner}/{name}")
        history = ref["target"]["history"]
        for commit in history["nodes"]:
            additions += commit["additions"]
            deletions += commit["deletions"]
            commits += 1
        page = history["pageInfo"]
        if not page["hasNextPage"]:
            return additions, deletions, commits
        cursor = page["endCursor"]


def collect():
    now = datetime.now(timezone.utc)
    profile = api(f"users/{LOGIN}")
    user_id, contributed, repos = repositories()
    created = datetime.fromisoformat(profile["created_at"].replace("Z", "+00:00"))
    commits, additions, deletions, history_commits = 0, 0, 0, 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(yearly_commits, year, now): f"year {year}"
                   for year in range(created.year, now.year + 1)}
        futures.update({pool.submit(repository_lines, repo, user_id): repo["nameWithOwner"]
                        for repo in repos})
        for future in as_completed(futures):
            label = futures[future]
            try:
                value = future.result()
            except Exception as exc:
                raise RuntimeError(f"Unable to measure {label}: {exc}") from exc
            if label.startswith("year "):
                commits += value
            else:
                a, d, c = value
                additions += a
                deletions += d
                history_commits += c
    languages = {}
    for repo in repos:
        if not repo["isFork"]:
            for language in repo["languages"]["nodes"]:
                languages[language["name"]] = languages.get(language["name"], 0) + 1
    return dict(login=LOGIN, profile=profile, repos=profile["public_repos"],
                contributed=contributed, stars=sum(r["stargazerCount"] for r in repos),
                commits=commits, additions=additions, deletions=deletions,
                history_commits=history_commits, followers=profile["followers"],
                languages=sorted(languages, key=lambda key: (-languages[key], key))[:3],
                scope="public + private" if include_private() else "public",
                updated=now.strftime("%Y-%m-%d %H:%M UTC"))


def dotted(key, value, width=56):
    """Never allow an overlong field to silently overflow the information column."""
    dots = width - len(key) - len(value) - 3
    if dots < 0:
        raise ValueError(f"Field is too wide ({key!r}, {value!r})")
    return [("k", key), ("d", " " + "." * dots + " "), ("v", value)]


def render(stats, theme):
    colors = PALETTES[theme]
    art_lines = ART.strip("\n").split("\n")
    assert max(map(len, art_lines)) <= 46
    assert len(art_lines) <= 28
    profile = stats["profile"]
    rows = [[("h", LOGIN + "@github")], [("d", "─" * 42)],
            dotted("Host", "GitHub"),
            dotted("Account since", profile["created_at"][:10])]
    if profile.get("location"):
        rows.append(dotted("Location", profile["location"][:30]))
    rows.append(dotted("Languages", " / ".join(stats["languages"])))
    rows += [[], [("d", "─ Contact " + "─" * 32)]]
    if profile.get("email"):
        rows.append(dotted("Email", profile["email"]))
    if profile.get("blog"):
        site = profile["blog"].removeprefix("https://").removeprefix("http://").rstrip("/")
        rows.append(dotted("Site", site))
    if profile.get("twitter_username"):
        rows.append(dotted("X", "@" + profile["twitter_username"]))
    rows += [[], [("d", "─ GitHub Stats " + "─" * 27)],
             dotted("Repos {Contributed}", f'{stats["repos"]} {{{stats["contributed"]}}}'),
             dotted("Stars", f'{stats["stars"]:,}'),
             dotted("Commits | Followers", f'{stats["commits"]:,} | {stats["followers"]:,}')]
    a, d = stats["additions"], stats["deletions"]
    loc = dotted("Lines of Code", f'{a-d:,} (+{a:,} -{d:,})')
    loc = loc[:2] + [("v", f"{a-d:,} ("), ("g", f"+{a:,}"), ("v", " "),
                      ("r", f"-{d:,}"), ("v", ")")]
    rows += [loc, dotted("Scope", stats["scope"] + " / default branches"),
             dotted("Updated", stats["updated"])]
    if len(rows) > 21:
        raise ValueError("Information column exceeds canvas height")
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="840" height="500" viewBox="0 0 840 500"',
             '     font-family="Consolas, Menlo, monospace" font-size="13px" role="img" aria-labelledby="title desc">',
             '<title id="title">shiheng2016 — GitHub terminal profile</title>',
             '<desc id="desc">ASCII avatar and automatically refreshed GitHub statistics. '
             'Commits are contribution-calendar commits. Lines of Code counts authored changes '
             'on owned repositories’ default branches, including forks, not current source size.</desc>',
             f'<rect x="0.5" y="0.5" width="839" height="499" rx="10" fill="{colors["bg"]}" stroke="{colors["border"]}"/>']
    y_top = 40 + (500 - 40 - len(art_lines) * 15) / 2
    for i, line in enumerate(art_lines):
        parts.append(f'<text x="10" y="{y_top+i*15:g}" xml:space="preserve" fill="{colors["art"]}">{escape(line)}</text>')
    for i, spans in enumerate(rows):
        content = "".join(f'<tspan fill="{colors[token]}">{escape(text)}</tspan>' for token, text in spans)
        parts.append(f'<text x="390" y="{45+i*21}" xml:space="preserve">{content}</text>')
    parts.append('</svg>')
    return "\n".join(parts) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-json", type=Path, help="Render a saved snapshot without API access")
    parser.add_argument("--snapshot", type=Path, help="Write collected statistics for comparison (keep private snapshots private)")
    args = parser.parse_args()
    stats = json.loads(args.from_json.read_text()) if args.from_json else collect()
    # Finish all collection and render both themes before replacing either artifact.
    rendered = {theme: render(stats, theme) for theme in PALETTES}
    for theme, svg in rendered.items():
        target = ROOT / "img" / (theme + ".svg")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".svg.tmp")
        tmp.write_text(svg, encoding="utf-8")
        tmp.replace(target)
    if args.snapshot:
        args.snapshot.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f'Rendered {stats["scope"]} statistics: {stats["repos"]} public repos, '
          f'{stats["stars"]} stars, {stats["commits"]} commits, '
          f'+{stats["additions"]}/-{stats["deletions"]} authored lines.')


if __name__ == "__main__":
    main()
