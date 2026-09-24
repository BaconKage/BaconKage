"""Rebuild the profile card and the live section of README.md.

Runs daily in GitHub Actions (see .github/workflows/profile.yml). Standard
library only, so the workflow needs no install step.

    GITHUB_TOKEN=... python scripts/build_profile.py
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import math
import os
import re
import urllib.request
from html import escape
from pathlib import Path

USER = "BaconKage"
CDC_REPO = "BaconKage/content-death-clock"
ROOT = Path(__file__).resolve().parent.parent
TOKEN = os.environ.get("GITHUB_TOKEN", "")

# Commits that are machine-made or say nothing about the work.
SKIP_REPOS = {f"{USER}/{USER}"}
SKIP_MESSAGE = re.compile(r"^(data:|merge |wip\b|update readme)", re.I)


# --------------------------------------------------------------------------- #
# GitHub API
# --------------------------------------------------------------------------- #

def api(path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"https://api.github.com/{path.lstrip('/')}",
        data=json.dumps(body).encode() if body else None,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{USER}-profile-builder",
            **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def profile_stats() -> dict:
    query = """
    query($login: String!) {
      user(login: $login) {
        repositories(ownerAffiliations: OWNER, privacy: PUBLIC, isFork: false) { totalCount }
        contributionsCollection { contributionCalendar { totalContributions } }
      }
    }"""
    user = api("graphql", {"query": query, "variables": {"login": USER}})["data"]["user"]
    return {
        "repos": user["repositories"]["totalCount"],
        "contributions": user["contributionsCollection"]["contributionCalendar"]["totalContributions"],
    }


def collector_stats() -> dict | None:
    """Latest panel totals from Content Death Clock's own cycle reports.

    Each collection cycle writes data/bronze/_cycles/<cycle_id>.json with the
    panel size *before* it ran, so the newest YouTube report is the running
    total. Returns None rather than guessing if the layout ever changes.
    """
    try:
        bronze = api(f"repos/{CDC_REPO}/contents/data/bronze")
        cycles_sha = next(e["sha"] for e in bronze if e["name"] == "_cycles")
        tree = api(f"repos/{CDC_REPO}/git/trees/{cycles_sha}")["tree"]
        youtube = sorted(e["path"] for e in tree if not e["path"].startswith("ig-"))
        first, last = youtube[0], youtube[-1]
        report = api(f"repos/{CDC_REPO}/contents/data/bronze/_cycles/{last}")
        panel = json.loads(base64.b64decode(report["content"]))["panel_before"]
        return {
            "snapshots": panel["snapshots_total"],
            "videos": panel["posts_total"],
            "active": panel["posts_active"],
            "cycles": len(youtube),
            "since": dt.datetime.strptime(first[:10], "%Y-%m-%d"),
            "last": dt.datetime.strptime(last[:13], "%Y-%m-%dT%H").replace(tzinfo=dt.timezone.utc),
        }
    except Exception as exc:  # the card must still build if the collector moves
        print(f"collector stats unavailable: {exc!r}")
        return None


def recent_work(limit: int = 5) -> list[dict]:
    """Newest meaningful public commit per repository, most recent first."""
    items = api(f"search/commits?q=author:{USER}&sort=author-date&order=desc&per_page=50")["items"]
    seen, out = set(), []
    for item in items:
        repo = item["repository"]["full_name"]
        message = item["commit"]["message"].split("\n")[0].strip()
        if repo in SKIP_REPOS or repo in seen or SKIP_MESSAGE.match(message):
            continue
        seen.add(repo)
        out.append({
            "repo": repo,
            "url": item["html_url"],
            "message": re.sub(r"\s*\(#\d+\)$", "", message),
            "date": dt.datetime.fromisoformat(item["commit"]["author"]["date"]),
        })
        if len(out) == limit:
            break
    return out


# --------------------------------------------------------------------------- #
# Card
# --------------------------------------------------------------------------- #

THEMES = {
    "dark": dict(bg="#0d1117", border="#30363d", text="#e6edf3", muted="#7d8590",
                 accent="#f59e0b", grid="#f59e0b", grid_op=0.16, live="#3fb950", glow=0.35),
    "light": dict(bg="#ffffff", border="#d0d7de", text="#1f2328", muted="#656d76",
                  accent="#b45309", grid="#b45309", grid_op=0.20, live="#1a7f37", glow=0.18),
}

W, H = 880, 360
LENS_CX, LENS_CY, EINSTEIN_R = 160, 197, 58


def lens(x: float, y: float) -> tuple[float, float]:
    """Where a point-mass lens images a background point (outer image only).

    Lens equation beta = theta - thetaE^2 / theta, solved for theta. Straight
    grid lines behind the lens bend into the arcs you see around the ring.
    """
    dx, dy = x - LENS_CX, y - LENS_CY
    beta = math.hypot(dx, dy)
    if beta < 1e-6:
        return LENS_CX, LENS_CY - EINSTEIN_R
    theta = (beta + math.sqrt(beta * beta + 4 * EINSTEIN_R ** 2)) / 2
    return LENS_CX + dx * theta / beta, LENS_CY + dy * theta / beta


def lensed_grid() -> str:
    x0, x1, y0, y1, step = 20, 300, 52, 344, 24
    lines = []
    for i in range(int((x1 - x0) / step) + 1):
        gx = x0 + i * step
        pts = [lens(gx, y0 + k * 2) for k in range(int((y1 - y0) / 2) + 1)]
        lines.append(pts)
    for j in range(int((y1 - y0) / step) + 1):
        gy = y0 + j * step
        pts = [lens(x0 + k * 2, gy) for k in range(int((x1 - x0) / 2) + 1)]
        lines.append(pts)

    def inside(p):
        return x0 - 4 <= p[0] <= x1 + 4 and y0 - 4 <= p[1] <= y1 + 4

    paths = []
    for pts in lines:
        seg = []
        for p in pts + [None]:
            if p is not None and inside(p):
                seg.append(p)
            elif len(seg) > 1:
                paths.append("M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in seg))
                seg = []
            else:
                seg = []
    return " ".join(paths)


def fmt(n: int) -> str:
    return f"{n:,}"


def card_lines(stats: dict, cdc: dict | None, now: dt.datetime) -> list[tuple[str, str]]:
    lines = [
        ("Role", "Full-stack &amp; AI engineer"),
        ("Study", "Data Science, RV University · final year"),
        ("Builds", "AI systems that say when they don't know"),
        ("Paper", "AI &amp; Society · Springer, 2025"),
        ("Stack", "Python · TypeScript · FastAPI · Next.js"),
        ("Also", "Node · MongoDB · OpenCV · Three.js · Ollama"),
        ("", ""),
        ("GitHub", f"{fmt(stats['repos'])} repos · {fmt(stats['contributions'])} contributions this year"),
    ]
    if cdc:
        lines.append(("Collector", f"{fmt(cdc['snapshots'])} snapshots · {fmt(cdc['videos'])} videos"))
    lines.append(("Updated", now.strftime("%d %b %Y")))
    return lines


def render_card(theme: str, stats: dict, cdc: dict | None, now: dt.datetime) -> str:
    t = THEMES[theme]
    tx, ty, lh = 340, 104, 23
    key_w = 12  # characters, dotted leader included

    rows = []
    for i, (key, value) in enumerate(card_lines(stats, cdc, now)):
        if not key:
            continue
        y = ty + i * lh
        dots = " " + "." * (key_w - len(key) - 1) + " "
        live = ""
        if key == "Collector":
            live = (f'<tspan fill="{t["live"]}" class="pulse"> ●</tspan>'
                    f'<tspan fill="{t["muted"]}"> live</tspan>')
        rows.append(
            f'<text x="{tx}" y="{y}" xml:space="preserve">'
            f'<tspan fill="{t["accent"]}">{key}</tspan>'
            f'<tspan fill="{t["muted"]}">{dots}</tspan>'
            f'<tspan fill="{t["text"]}">{value}</tspan>{live}</text>'
        )

    cursor_y = ty + len(card_lines(stats, cdc, now)) * lh + 4
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="Shubhang Srinivas Varda. Full-stack and AI engineer, Data Science at RV University.">
<title>Shubhang Srinivas Varda — full-stack &amp; AI engineer</title>
<style>
  text {{ font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace; font-size: 15px; }}
  .h {{ font-size: 18px; font-weight: 700; }}
  .pulse {{ animation: pulse 2.4s ease-in-out infinite; }}
  .ring {{ animation: breathe 6s ease-in-out infinite; transform-origin: {LENS_CX}px {LENS_CY}px; }}
  .cursor {{ animation: blink 1.1s steps(1) infinite; }}
  @keyframes pulse {{ 50% {{ opacity: .25; }} }}
  @keyframes breathe {{ 50% {{ opacity: .55; }} }}
  @keyframes blink {{ 50% {{ opacity: 0; }} }}
  @media (prefers-reduced-motion: reduce) {{ .pulse, .ring, .cursor {{ animation: none; }} }}
</style>
<defs>
  <radialGradient id="glow" cx="50%" cy="50%" r="50%">
    <stop offset="0%" stop-color="{t['accent']}" stop-opacity=".0"/>
    <stop offset="72%" stop-color="{t['accent']}" stop-opacity=".0"/>
    <stop offset="84%" stop-color="{t['accent']}" stop-opacity="{t['glow']}"/>
    <stop offset="100%" stop-color="{t['accent']}" stop-opacity="0"/>
  </radialGradient>
  <clipPath id="pane"><rect x="12" y="46" width="296" height="302" rx="8"/></clipPath>
</defs>

<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="12" fill="{t['bg']}" stroke="{t['border']}"/>
<line x1="0" y1="34" x2="{W}" y2="34" stroke="{t['border']}"/>
<text x="20" y="22" fill="{t['muted']}" style="font-size:12px">~/BaconKage — profile.svg</text>
<text x="{W - 20}" y="22" fill="{t['muted']}" text-anchor="end" style="font-size:12px">Bengaluru, IN</text>

<g clip-path="url(#pane)">
  <path d="{lensed_grid()}" fill="none" stroke="{t['grid']}" stroke-opacity="{t['grid_op']}" stroke-width="1"/>
  <g class="ring">
    <circle cx="{LENS_CX}" cy="{LENS_CY}" r="{EINSTEIN_R * 1.6:.0f}" fill="url(#glow)"/>
    <circle cx="{LENS_CX}" cy="{LENS_CY}" r="{EINSTEIN_R}" fill="none" stroke="{t['accent']}" stroke-width="1.6"/>
  </g>
  <circle cx="{LENS_CX}" cy="{LENS_CY}" r="{EINSTEIN_R - 14}" fill="{t['bg']}"/>
  <circle cx="{LENS_CX}" cy="{LENS_CY}" r="2.5" fill="{t['accent']}"/>
</g>

<text x="{tx}" y="{ty - 36}" class="h"><tspan fill="{t['text']}">Shubhang Srinivas Varda</tspan><tspan fill="{t['muted']}" style="font-weight:400;font-size:14px">  @{USER}</tspan></text>
<line x1="{tx}" y1="{ty - 24}" x2="{W - 28}" y2="{ty - 24}" stroke="{t['border']}"/>
{chr(10).join(rows)}
<text x="{tx}" y="{cursor_y}"><tspan fill="{t['accent']}">❯</tspan><tspan class="cursor" fill="{t['text']}"> █</tspan></text>
</svg>
"""


# --------------------------------------------------------------------------- #
# README live section
# --------------------------------------------------------------------------- #

def ago(then: dt.datetime, now: dt.datetime) -> str:
    minutes = int((now - then).total_seconds() // 60)
    if minutes < 90:
        return f"{max(minutes, 1)} min ago"
    return f"{minutes // 60} h ago"


def live_section(cdc: dict | None, work: list[dict], now: dt.datetime) -> str:
    out = []
    if cdc:
        out.append(
            f"**[Content Death Clock](https://github.com/{CDC_REPO}) collector** &nbsp;·&nbsp; "
            f"running unattended on GitHub Actions since {cdc['since']:%d %b}: "
            f"**{fmt(cdc['snapshots'])}** engagement snapshots of **{fmt(cdc['videos'])}** YouTube videos "
            f"across {fmt(cdc['cycles'])} collection cycles, {fmt(cdc['active'])} still being tracked. "
            f"Last cycle {ago(cdc['last'], now)} when this was built."
        )
        out.append("")
    if work:
        out.append("**Latest commits**")
        out.append("")
        for w in work:
            name = w["repo"].split("/", 1)[1] if w["repo"].startswith(f"{USER}/") else w["repo"]
            out.append(
                f"- [`{name}`](https://github.com/{w['repo']}) &nbsp;{escape(w['message'])} "
                f"<sub>[{w['date']:%d %b}]({w['url']})</sub>"
            )
        out.append("")
    out.append(f"<sub>Rebuilt daily by [a GitHub Action](.github/workflows/profile.yml) "
               f"· last run {now:%d %b %Y, %H:%M} UTC</sub>")
    return "\n".join(out)


def replace_block(text: str, name: str, body: str) -> str:
    pattern = re.compile(rf"(<!-- {name}:start -->\n).*?(<!-- {name}:end -->)", re.S)
    if not pattern.search(text):
        raise SystemExit(f"README is missing the {name} markers")
    return pattern.sub(lambda m: m.group(1) + body + "\n" + m.group(2), text)


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    now = dt.datetime.now(dt.timezone.utc)
    stats = profile_stats()
    cdc = collector_stats()
    work = recent_work()

    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    for theme in THEMES:
        write(assets / f"card-{theme}.svg", render_card(theme, stats, cdc, now))

    readme = ROOT / "README.md"
    write(readme, replace_block(readme.read_text(encoding="utf-8"), "live", live_section(cdc, work, now)))
    print(json.dumps({"stats": stats, "collector": bool(cdc), "commits": len(work)}))


if __name__ == "__main__":
    main()
