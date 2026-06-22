#!/usr/bin/env python3
"""
Render a Markdown table from results.json produced by drupal_ai_dependents.py.

Usage:
  python3 render_md.py results.json [-o output.md]
"""

import argparse
import json
import sys


def render_md(payload: dict) -> str:
    """Return a Markdown string from a results.json payload."""
    rows    = payload["modules"]
    today   = payload["generated"]
    v_label = "/".join(str(v) for v in sorted(payload["drupal_versions"]))
    count   = len(rows)

    lines = [
        f"# Drupal Modules with a Hard Dependency on [AI](https://www.drupal.org/project/ai)\n",
        f"*Generated {today} · {count} modules · Drupal {v_label} compatible · all stability levels*\n",
        "| Module | URL | Latest Version | Release Date | Usage (installs) |",
        "|--------|-----|:--------------:|:------------:|----------------:|",
    ]
    for r in rows:
        usage_str = f"{r['usage']:,}" if r["usage"] else "—"
        lines.append(
            f"| {r['name']} | {r['url']} | `{r['version']}` | {r['release_date']} | {usage_str} |"
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
        print(f"Wrote {len(payload['modules'])} rows to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
