#!/usr/bin/env python3
"""
Render a Markdown table from results.json produced by drupal_ai_dependents.py.

Usage:
  python3 render_md.py results.json [-o output.md] [--history history.json]
"""

import argparse
import html
import json
import sys
from datetime import datetime

# Security advisory coverage icons.
# Filled shield — covered + stable release — Drupal.org's own SVG via img tag.
SECURITY_COVERED_STABLE_MD = (
    '<img src="images/shield-icon-black.svg" width="16" height="16" style="opacity:0.5" '
    'alt="Security covered (stable)">'
)
# Outline shield — covered + pre-release — self-contained SVG data URI.
SECURITY_COVERED_PRERELEASE_MD = (
    '<img src="data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org'
    '%2F2000%2Fsvg%27%20width%3D%2716%27%20height%3D%2716%27%20viewBox%3D%270'
    '%200%2024%2024%27%20fill%3D%27none%27%20stroke%3D%27%23444%27%20'
    'stroke-width%3D%272.5%27%3E%3Cpath%20d%3D%27M12%2022s8-4%208-10V5l-8-3'
    '-8%203v7c0%206%208%2010%208%2010z%27%2F%3E%3C%2Fsvg%3E" '
    'width="16" height="16" style="opacity:0.5" alt="Security covered (pre-release)">'
)
SECURITY_NOT_COVERED_EMOJI = "🚫"


def _label_cell(r: dict) -> str:
    """Build the label table cell. Project link, with the description."""
    cell = f"[{r['label']}]({r['url']})"
    desc = r.get("description")
    if desc:
        safe = html.escape(desc, quote=False).replace("|", "\\|")
        safe = " ".join(safe.split())  # collapse any newlines/runs of whitespace
        cell += f"<br><sub>{safe}</sub>"
    return cell


def _categories_cell(r: dict) -> str:
    """Build the categories table cell: comma-separated category names.

    Escaped and pipe-guarded like _label_cell so it can't break the table row.
    Reads categories via .get() so pre-category JSON renders a plain dash.
    """
    cats = r.get("categories", [])
    if not cats:
        return "—"
    return html.escape(", ".join(cats), quote=False).replace("|", "\\|")


def _trend(history: dict, machine_name: str, metric: str):
    """Compare the latest two history.json points for one package.

    Returns (percent_change, direction, raw_delta, prev_date) — direction is
    "up"/"down"/"flat", raw_delta is the plain signed difference (e.g. +34),
    and prev_date is the earlier point's "date" string (for the tooltip) —
    or None if there's no history, fewer than two points, or the baseline
    point is missing/null/zero (can't compute a % change from that). Kept as
    a local copy in both renderers rather than a shared import — see
    CATEGORY_ORDER in render_html.py for the same "no cross-file import"
    convention.
    """
    points = history.get(machine_name)
    if not points or len(points) < 2:
        return None
    prev_point, latest_point = points[-2], points[-1]
    prev_val = prev_point.get(metric)
    latest_val = latest_point.get(metric)
    if not prev_val or latest_val is None:
        return None
    raw_delta = latest_val - prev_val
    pct = raw_delta / prev_val * 100
    direction = "up" if pct > 0 else "down" if pct < 0 else "flat"
    return pct, direction, raw_delta, prev_point.get("date")


_TREND_ARROW = {"up": "▲", "down": "▼", "flat": "●"}


def _format_trend_date(date_str: str | None) -> str:
    """'2026-09-01' -> 'Sep 1 2026', for the trend tooltip."""
    if not date_str:
        return "—"
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%b %-d %Y")
    except ValueError:
        return date_str


def _trend_title(raw_delta: int, prev_date: str | None) -> str:
    """'+34 since Sep 1 2026' / '-543 since Aug 15 2026' — the trend tooltip text."""
    return f"{raw_delta:+,} since {_format_trend_date(prev_date)}"


def _trend_cell(history: dict, machine_name: str, metric: str) -> str:
    """Build the Trend cell: arrow + percent change, with a tooltip showing
    the raw value change and the date of the previous run. '—' with no history.
    """
    result = _trend(history, machine_name, metric)
    if result is None:
        return "—"
    pct, direction, raw_delta, prev_date = result
    title = html.escape(_trend_title(raw_delta, prev_date), quote=True)
    return f'<span title="{title}">{_TREND_ARROW[direction]} {pct:+.1f}%</span>'


def render_md(payload: dict, history: dict | None = None) -> str:
    """Return a Markdown string from a results.json payload.

    `history` is the parsed history.json dict (see drupal_ai_dependents.py's
    History section) used to compute each row's Trend cell. Defaults to {}
    (blank Trend column) so this still works if a caller omits it.
    """
    history      = history or {}
    rows         = payload["modules"]
    recipe_rows  = payload.get("recipes", [])  # back-compat: older files have no "recipes" key
    today        = payload["generated"]
    v_label      = "/".join(str(v) for v in sorted(payload["drupal_versions"]))
    count        = len(rows)
    recipe_count = len(recipe_rows)
    # Back-compat: files from before --full-ecosystem existed were all
    # dependency-only crawls, so treat a missing key as False.
    full_ecosystem = payload.get("full_ecosystem", False)

    # Without --full-ecosystem every row requires drupal/ai, so the column
    # would be a wall of identical ticks — state the scope in the header
    # instead and drop the column.
    if full_ecosystem:
        scope_line = (
            "*Modules with a hard dependency on"
            " [drupal/ai](https://www.drupal.org/project/ai) or filed under"
            " drupal.org's \"Artificial Intelligence (AI)\" project category*\n"
        )
        header_row = ("| Label | URL | Latest Version | Release Date | Security"
                      " | Usage | Trend | drupal/ai | Categories |")
        divider_row = ("|-------|-----|:--------------:|:------------:|"
                       ":------------------:|----------------:|:-----:|:---------:|------------|")
    else:
        scope_line = ("*Only includes projects with a dependency on the"
                      " [Drupal AI module](https://www.drupal.org/project/ai)*\n")
        header_row = ("| Label | URL | Latest Version | Release Date | Security"
                      " | Usage | Trend | Categories |")
        divider_row = ("|-------|-----|:--------------:|:------------:|"
                       ":------------------:|----------------:|:-----:|------------|")

    lines = [
        f"# Drupal AI Modules and Recipes\n",
        scope_line,
        f"*Generated {today} · {count} modules · {recipe_count} recipes ·"
        f" Drupal {v_label} compatible · all stability levels*\n",
        "Sponsored by DrupalEasy's [*Responsible Drupal AI Basics*](https://drupaleasy.com/rdab) course\n",
        "## Modules\n",
        header_row,
        divider_row,
    ]
    for r in rows:
        usage_str = f"{r['usage']:,}" if r["usage"] else "—"
        # Back-compat: results.json files from before the AI-category source
        # only ever contained hard dependents, so default to True.
        requires_ai_cell = (f" {'✓' if r.get('requires_ai', True) else '—'} |"
                            if full_ecosystem else "")
        if r["security_covered"]:
            stability = r.get("stability", "stable")
            security_str = (SECURITY_COVERED_STABLE_MD
                            if stability == "stable"
                            else SECURITY_COVERED_PRERELEASE_MD)
        else:
            security_str = SECURITY_NOT_COVERED_EMOJI
        lines.append(
            f"| {_label_cell(r)} | {r['url']} | `{r['version']}` |"
            f" {r['release_date']} | {security_str} | {usage_str} |"
            f" {_trend_cell(history, r['machine_name'], 'usage')} |"
            f"{requires_ai_cell} {_categories_cell(r)} |"
        )

    legend = (
        f"\n*Security coverage:*"
        f" {SECURITY_COVERED_STABLE_MD} Covered (stable release)"
        f" &nbsp;·&nbsp; {SECURITY_COVERED_PRERELEASE_MD} Covered (pre-release)"
        f" &nbsp;·&nbsp; {SECURITY_NOT_COVERED_EMOJI} Not covered by security advisory policy"
    )
    if full_ecosystem:
        legend += (f"<br>*drupal/ai:* ✓ Hard composer dependency on drupal/ai"
                   f" &nbsp;·&nbsp; — AI project category only")
    lines.append(legend)

    # Recipes have no Drupal.org usage tracking and no meaningful security-advisory
    # status, so those two columns are omitted. Packagist total downloads are
    # available and provide a comparable popularity signal, and are what the
    # Trend column tracks for recipes (the only metric history.json has for them).
    lines.append("\n## Recipes\n")
    # Column order matches render_html.py's recipes tab: Trend (which tracks
    # downloads) sits directly after the downloads column it derives from.
    lines.append("| Label | URL | Latest Version | Release Date | Packagist downloads | Trend | Packagist stars | Categories |")
    lines.append("|-------|-----|:--------------:|:------------:|--------------------:|:-----:|----------------:|------------|")
    for r in recipe_rows:
        downloads = r.get("downloads")
        downloads_str = f"{downloads:,}" if downloads is not None else "—"
        stars = r.get("stars")
        stars_str = f"{stars:,}" if stars is not None else "—"
        lines.append(
            f"| {_label_cell(r)} | {r['url']} | `{r['version']}` |"
            f" {r['release_date']} | {downloads_str} |"
            f" {_trend_cell(history, r['machine_name'], 'downloads')} |"
            f" {stars_str} | {_categories_cell(r)} |"
        )

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("json_file", metavar="FILE",
                        help="Path to results.json from drupal_ai_dependents.py")
    parser.add_argument("--output", "-o", metavar="OUT",
                        help="Write to OUT instead of stdout")
    parser.add_argument(
        "--history", metavar="FILE", default="history.json",
        help="Path to history.json (written by drupal_ai_dependents.py) used "
             "to compute the Trend column. Defaults to history.json alongside "
             "the results file. Missing file is fine — the Trend column is "
             "just blank ('—') for every row.",
    )
    args = parser.parse_args()

    with open(args.json_file, encoding="utf-8") as fh:
        payload = json.load(fh)

    try:
        with open(args.history, encoding="utf-8") as fh:
            history = json.load(fh)
    except FileNotFoundError:
        history = {}

    output = render_md(payload, history)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(output)
        module_count = len(payload['modules'])
        recipe_count = len(payload.get('recipes', []))
        print(f"Wrote {module_count} modules and {recipe_count} recipes to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
