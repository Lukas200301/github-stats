#!/usr/bin/python3

import asyncio
import html
import os
import re
from datetime import datetime, timezone

import aiohttp

from github_stats import Stats


################################################################################
# Helper Functions
################################################################################


def generate_output_folder() -> None:
    """
    Create the output folder if it does not already exist
    """
    if not os.path.isdir("generated"):
        os.mkdir("generated")


################################################################################
# Individual Image Generation Functions
################################################################################


async def generate_overview(s: Stats) -> None:
    """
    Generate an SVG badge with summary statistics
    :param s: Represents user's GitHub statistics
    """
    with open("templates/overview.svg", "r") as f:
        output = f.read()

    output = re.sub("{{ name }}", await s.name, output)
    output = re.sub("{{ stars }}", f"{await s.stargazers:,}", output)
    output = re.sub("{{ forks }}", f"{await s.forks:,}", output)
    output = re.sub("{{ contributions }}", f"{await s.total_contributions:,}", output)
    changed = (await s.lines_changed)[0] + (await s.lines_changed)[1]
    output = re.sub("{{ lines_changed }}", f"{changed:,}", output)
    output = re.sub("{{ views }}", f"{await s.views:,}", output)
    output = re.sub("{{ repos }}", f"{len(await s.repos):,}", output)

    generate_output_folder()
    with open("generated/overview.svg", "w") as f:
        f.write(output)


async def generate_languages(s: Stats) -> None:
    """
    Generate an SVG badge with summary languages used
    :param s: Represents user's GitHub statistics
    """
    with open("templates/languages.svg", "r") as f:
        output = f.read()

    progress = ""
    lang_list = ""
    sorted_languages = sorted(
        (await s.languages).items(), reverse=True, key=lambda t: t[1].get("size")
    )
    delay_between = 150
    for i, (lang, data) in enumerate(sorted_languages):
        color = data.get("color")
        color = color if color is not None else "#000000"
        progress += (
            f'<span style="background-color: {color};'
            f'width: {data.get("prop", 0):0.3f}%;" '
            f'class="progress-item"></span>'
        )
        lang_list += f"""
<li style="animation-delay: {i * delay_between}ms;">
<svg xmlns="http://www.w3.org/2000/svg" class="octicon" style="fill:{color};"
viewBox="0 0 16 16" version="1.1" width="16" height="16"><path
fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8z"></path></svg>
<span class="lang">{lang}</span>
<span class="percent">{data.get("prop", 0):0.2f}%</span>
</li>

"""

    output = re.sub(r"{{ progress }}", progress, output)
    output = re.sub(r"{{ lang_list }}", lang_list, output)

    generate_output_folder()
    with open("generated/languages.svg", "w") as f:
        f.write(output)


def time_ago(iso: str) -> str:
    """
    Turn an ISO timestamp from the GitHub API into "today", "3 days ago", ...
    """
    then = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - then).days
    if days < 1:
        return "today"
    if days == 1:
        return "yesterday"
    for size, unit in ((365, "year"), (30, "month"), (7, "week"), (1, "day")):
        if days >= size:
            n = days // size
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "today"


async def generate_recent(s: Stats) -> None:
    """
    Generate an SVG card with the most recently pushed public repositories
    :param s: Represents user's GitHub statistics
    """
    with open("templates/recent.svg", "r") as f:
        output = f.read()

    # Repos that are pushed every night by bots would always be on top
    hidden = os.getenv("RECENT_EXCLUDED")
    hidden = (
        {x.strip().lower() for x in hidden.split(",") if x.strip()}
        if hidden is not None
        else {"github-stats", s.username.lower(), f"{s.username.lower()}.github.io"}
    )
    hidden |= {x.lower() for x in (s._exclude_repos or set())}
    count = int(os.getenv("RECENT_COUNT", "3"))

    # /users/:name/repos only ever returns public repositories
    repos = await s.queries.query_rest(
        f"/users/{s.username}/repos",
        {"type": "owner", "sort": "pushed", "per_page": "30"},
    )
    if not isinstance(repos, list):
        repos = []

    def visible(color):
        # Very dark language colours (e.g. PowerShell) vanish on the navy card
        if not color or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            return "#8ea2ff"
        r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
        return color if 0.2126 * r + 0.7152 * g + 0.0722 * b > 60 else "#6c789c"

    colors = {k: visible(v.get("color")) for k, v in (await s.languages).items()}
    picked = [
        r
        for r in repos
        if not r.get("private")
        and not r.get("archived")
        and not (s._ignore_forked_repos and r.get("fork"))
        and r.get("name", "").lower() not in hidden
        and r.get("full_name", "").lower() not in hidden
    ][:count]

    items = ""
    for r in picked:
        lang = r.get("language")
        lang_html = (
            f'<span class="lang"><span class="dot" style="background:'
            f'{colors.get(lang) or "#8ea2ff"}"></span>{html.escape(lang)}</span>'
            if lang
            else ""
        )
        desc = html.escape(r.get("description") or "No description")
        items += (
            f'<li><div class="row"><span class="name">{html.escape(r["name"])}</span>'
            f'{lang_html}<span class="ago">{time_ago(r["pushed_at"])}</span></div>'
            f'<div class="desc">{desc}</div></li>\n'
        )
    if not items:
        items = '<li><div class="desc">Nothing public pushed yet.</div></li>'

    updated = datetime.now(timezone.utc).strftime("%d %b %Y")
    output = output.replace("{{ repo_list }}", items).replace("{{ updated }}", updated)

    generate_output_folder()
    with open("generated/recent.svg", "w") as f:
        f.write(output)


################################################################################
# Main Function
################################################################################


async def main() -> None:
    """
    Generate all badges
    """
    access_token = os.getenv("ACCESS_TOKEN")
    if not access_token:
        # access_token = os.getenv("GITHUB_TOKEN")
        raise Exception("A personal access token is required to proceed!")
    user = os.getenv("GITHUB_ACTOR")
    if user is None:
        raise RuntimeError("Environment variable GITHUB_ACTOR must be set.")
    exclude_repos = os.getenv("EXCLUDED")
    excluded_repos = (
        {x.strip() for x in exclude_repos.split(",")} if exclude_repos else None
    )
    exclude_langs = os.getenv("EXCLUDED_LANGS")
    excluded_langs = (
        {x.strip() for x in exclude_langs.split(",")} if exclude_langs else None
    )
    # Convert a truthy value to a Boolean
    raw_ignore_forked_repos = os.getenv("EXCLUDE_FORKED_REPOS")
    ignore_forked_repos = (
        not not raw_ignore_forked_repos
        and raw_ignore_forked_repos.strip().lower() != "false"
    )
    async with aiohttp.ClientSession() as session:
        s = Stats(
            user,
            access_token,
            session,
            exclude_repos=excluded_repos,
            exclude_langs=excluded_langs,
            ignore_forked_repos=ignore_forked_repos,
        )
        await asyncio.gather(
            generate_languages(s), generate_overview(s), generate_recent(s)
        )


if __name__ == "__main__":
    asyncio.run(main())
