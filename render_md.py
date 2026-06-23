#!/usr/bin/env python3
"""
Render a Markdown table from results.json produced by drupal_ai_dependents.py.

Usage:
  python3 render_md.py results.json [-o output.md]
"""

import argparse
import json
import sys

# Security advisory coverage indicators — kept in sync with the constants of
# the same name in drupal_ai_dependents.py.
SECURITY_COVERED_EMOJI     = "✅"
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
        "| Label | machine name | URL | Latest Version | Release Date | Security coverage | Drupal.org usage |",
        "|-------|--------------|-----|:--------------:|:------------:|:------------------:|----------------:|",
    ]
    for r in rows:
        usage_str = f"{r['usage']:,}" if r["usage"] else "—"
        security_str = SECURITY_COVERED_EMOJI if r["security_covered"] else SECURITY_NOT_COVERED_EMOJI
        lines.append(
            f"| [{r['label']}]({r['url']}) | {r['machine_name']} | {r['url']} | `{r['version']}` |"
            f" {r['release_date']} | {security_str} | {usage_str} |"
        )

    # Recipes have no usage tracking and no meaningful security-advisory
    # status, so this table omits both columns rather than inventing an
    # "N/A" state for them.
    lines.append("\n## Recipes\n")
    lines.append("| Label | machine name | URL | Latest Version | Release Date |")
    lines.append("|-------|--------------|-----|:--------------:|:------------:|")
    for r in recipe_rows:
        lines.append(
            f"| [{r['label']}]({r['url']}) | {r['machine_name']} | {r['url']} | `{r['version']}` |"
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
