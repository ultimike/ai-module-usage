#!/usr/bin/env python3
"""
Render a Markdown table from results.json produced by drupal_ai_dependents.py.

Usage:
  python3 render_md.py results.json [-o output.md]
"""

import argparse
import json
import sys

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


def render_md(payload: dict) -> str:
    """Return a Markdown string from a results.json payload."""
    rows         = payload["modules"]
    recipe_rows  = payload.get("recipes", [])  # back-compat: older files have no "recipes" key
    today        = payload["generated"]
    v_label      = "/".join(str(v) for v in sorted(payload["drupal_versions"]))
    count        = len(rows)
    recipe_count = len(recipe_rows)

    lines = [
        f"# Drupal Modules and Recipes with a Hard Dependency on [AI](https://www.drupal.org/project/ai)\n",
        f"*Generated {today} · {count} modules · {recipe_count} recipes ·"
        f" Drupal {v_label} compatible · all stability levels*\n",
        "## Modules\n",
        "| Label | URL | Latest Version | Release Date | Security coverage | Drupal.org usage |",
        "|-------|-----|:--------------:|:------------:|:------------------:|----------------:|",
    ]
    for r in rows:
        usage_str = f"{r['usage']:,}" if r["usage"] else "—"
        if r["security_covered"]:
            stability = r.get("stability", "stable")
            security_str = (SECURITY_COVERED_STABLE_MD
                            if stability == "stable"
                            else SECURITY_COVERED_PRERELEASE_MD)
        else:
            security_str = SECURITY_NOT_COVERED_EMOJI
        lines.append(
            f"| [{r['label']}]({r['url']}) | {r['url']} | `{r['version']}` |"
            f" {r['release_date']} | {security_str} | {usage_str} |"
        )

    # Recipes have no usage tracking and no meaningful security-advisory
    # status, so this table omits both columns rather than inventing an
    # "N/A" state for them.
    lines.append("\n## Recipes\n")
    lines.append("| Label | URL | Latest Version | Release Date |")
    lines.append("|-------|-----|:--------------:|:------------:|")
    for r in recipe_rows:
        lines.append(
            f"| [{r['label']}]({r['url']}) | {r['url']} | `{r['version']}` |"
            f" {r['release_date']} |"
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
    args = parser.parse_args()

    with open(args.json_file, encoding="utf-8") as fh:
        payload = json.load(fh)

    output = render_md(payload)

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
