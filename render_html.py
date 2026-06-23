#!/usr/bin/env python3
"""
Render a self-contained HTML table from results.json produced by drupal_ai_dependents.py.

Features: sortable columns, name filter, stability-level checkboxes
(stable/rc/beta/alpha/dev), security coverage checkboxes (covered/not covered).

Usage:
  python3 render_html.py results.json -o output.html
"""

import argparse
import html
import json
import sys


STABILITY_ORDER = ["stable", "rc", "beta", "alpha", "dev"]

# Security advisory coverage indicators — kept in sync with the constants of
# the same name in drupal_ai_dependents.py.
SECURITY_COVERED_EMOJI     = "✅"
SECURITY_NOT_COVERED_EMOJI = "🚫"

# (display label, CSS class)
_STABILITY_META = {
    "stable": ("Stable", "stab-stable"),
    "rc":     ("RC",     "stab-rc"),
    "beta":   ("Beta",   "stab-beta"),
    "alpha":  ("Alpha",  "stab-alpha"),
    "dev":    ("Dev",    "stab-dev"),
}

SECURITY_ORDER = ["covered", "not-covered"]

# (display label, emoji) keyed by the same value stored in data-security.
_SECURITY_META = {
    "covered":     ("Covered", SECURITY_COVERED_EMOJI),
    "not-covered": ("Not covered", SECURITY_NOT_COVERED_EMOJI),
}

# CSS stored as a plain string (not an f-string) to avoid escaping every { }.
_CSS = """\
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      max-width: 1100px;
      margin: 2rem auto;
      padding: 0 1rem;
      color: #222;
      background: #f7f8fa;
    }
    h1 { font-size: 1.4rem; margin-bottom: 0.25rem; }
    h1 a { color: inherit; }
    .meta { color: #666; font-size: 0.875rem; margin-bottom: 1rem; }
    .controls {
      display: flex;
      align-items: center;
      gap: 1.5rem;
      margin-bottom: 1rem;
      flex-wrap: wrap;
    }
    #filter {
      padding: 0.45rem 0.75rem;
      font-size: 1rem;
      width: 300px;
      border: 1px solid #bbb;
      border-radius: 4px;
      outline-color: #2d6a9f;
    }
    .stab-filters {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      flex-wrap: wrap;
      font-size: 0.9rem;
    }
    .stab-filters label {
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 0.25rem;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      background: #fff;
      box-shadow: 0 1px 4px rgba(0,0,0,.1);
    }
    thead { background: #2d6a9f; color: #fff; }
    th {
      padding: 0.6rem 1rem;
      text-align: left;
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
    }
    th:hover { background: #245a8c; }
    th[data-sort="asc"]::after  { content: " ▲"; }
    th[data-sort="desc"]::after { content: " ▼"; }
    th:not([data-sort])::after  { content: " ⇅"; color: rgba(255,255,255,.5); }
    td {
      padding: 0.5rem 1rem;
      border-bottom: 1px solid #eee;
    }
    tr:last-child td { border-bottom: none; }
    tbody tr:hover td { background: #f0f7ff; }
    .col-version  { font-family: ui-monospace, monospace; white-space: nowrap; }
    .col-date     { white-space: nowrap; }
    .col-security { text-align: center; }
    th.col-security { text-align: center; }
    .col-usage    { text-align: right; }
    th.col-usage  { text-align: right; }
    #no-results {
      display: none;
      padding: 1.5rem;
      text-align: center;
      color: #888;
      background: #fff;
      border-top: 1px solid #eee;
    }
    .badge {
      display: inline-block;
      padding: 1px 6px;
      border-radius: 3px;
      font-size: 0.7rem;
      font-weight: 600;
      vertical-align: middle;
      margin-left: 4px;
    }
    .stab-stable { background: #d4edda; color: #155724; }
    .stab-rc     { background: #cce5ff; color: #004085; }
    .stab-beta   { background: #fff3cd; color: #856404; }
    .stab-alpha  { background: #fde8d8; color: #7d3800; }
    .stab-dev    { background: #e2e3e5; color: #383d41; }"""

# JS stored as a plain string (not an f-string) to avoid escaping every { }.
_JS = """\
    (function () {
      var thead    = document.querySelector('#tbl thead tr');
      var tbody    = document.getElementById('tbody');
      var filter   = document.getElementById('filter');
      var noRes    = document.getElementById('no-results');
      var sortCol  = -1, sortDir = 1;

      function cellVal(row, col) {
        return row.children[col].dataset.val;
      }

      function sortTable(col, type) {
        if (sortCol === col) {
          sortDir = -sortDir;
        } else {
          sortCol = col;
          sortDir = (type === 'num') ? -1 : 1;
        }
        var rows = Array.from(tbody.querySelectorAll('tr'));
        rows.sort(function (a, b) {
          var av = cellVal(a, col), bv = cellVal(b, col);
          if (type === 'num') {
            av = (av === '') ? -Infinity : Number(av);
            bv = (bv === '') ? -Infinity : Number(bv);
          } else {
            if (av === '' && bv === '') return 0;
            if (av === '') return 1;
            if (bv === '') return -1;
          }
          return (av < bv ? -1 : av > bv ? 1 : 0) * sortDir;
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
        Array.from(thead.children).forEach(function (th) { delete th.dataset.sort; });
        thead.children[col].dataset.sort = (sortDir === 1) ? 'asc' : 'desc';
      }

      Array.from(thead.querySelectorAll('th')).forEach(function (th) {
        th.addEventListener('click', function () {
          sortTable(Number(th.dataset.col), th.dataset.type);
        });
      });

      sortTable(5, 'num');

      function applyFilter() {
        var q = filter.value.toLowerCase();
        var stabChecked = new Set(
          Array.from(document.querySelectorAll('.stab-cb:checked'))
               .map(function (cb) { return cb.value; })
        );
        var secChecked = new Set(
          Array.from(document.querySelectorAll('.sec-cb:checked'))
               .map(function (cb) { return cb.value; })
        );
        var visible = 0;
        Array.from(tbody.querySelectorAll('tr')).forEach(function (row) {
          var nameMatch = cellVal(row, 0).toLowerCase().indexOf(q) !== -1;
          var stabMatch = stabChecked.has(row.dataset.stability);
          var secMatch = secChecked.has(row.dataset.security);
          var show = nameMatch && stabMatch && secMatch;
          row.style.display = show ? '' : 'none';
          if (show) visible++;
        });
        noRes.style.display = (visible === 0) ? '' : 'none';
      }

      filter.addEventListener('input', applyFilter);
      Array.from(document.querySelectorAll('.stab-cb, .sec-cb')).forEach(function (cb) {
        cb.addEventListener('change', applyFilter);
      });
    })();"""


def render_html(payload: dict) -> str:
    """Return a self-contained HTML string with sortable columns and stability filter."""
    rows    = payload["modules"]
    today   = payload["generated"]
    v_label = "/".join(str(v) for v in sorted(payload["drupal_versions"]))
    count   = len(rows)

    def esc(s) -> str:
        return html.escape(str(s), quote=True)

    row_lines = []
    for r in rows:
        stability    = r.get("stability", "stable")
        label_esc    = esc(r["label"])
        machine_esc  = esc(r["machine_name"])
        url_esc      = esc(r["url"])
        ver_raw      = r["version"]
        ver_disp     = esc(ver_raw) if ver_raw else "—"
        ver_val      = esc(ver_raw) if ver_raw else ""
        date         = r["release_date"]
        date_val     = "" if date == "—" else esc(date)
        usage_raw    = str(r["usage"]) if r["usage"] else ""
        usage_disp   = f"{r['usage']:,}" if r["usage"] else "—"
        sec_covered  = r["security_covered"]
        sec_disp     = SECURITY_COVERED_EMOJI if sec_covered else SECURITY_NOT_COVERED_EMOJI
        sec_val      = "1" if sec_covered else "0"
        sec_status   = "covered" if sec_covered else "not-covered"
        stab_label, css_class = _STABILITY_META.get(stability, ("Stable", "stab-stable"))
        badge = f'<span class="badge {css_class}">{stab_label}</span>'
        row_lines.append(
            f'      <tr data-stability="{esc(stability)}" data-security="{sec_status}">'
            f'<td data-val="{label_esc}"><a href="{url_esc}">{label_esc}</a></td>'
            f'<td data-val="{machine_esc}">{machine_esc}</td>'
            f'<td data-val="{ver_val}" class="col-version">{ver_disp}{badge}</td>'
            f'<td data-val="{date_val}" class="col-date">{esc(date)}</td>'
            f'<td data-val="{sec_val}" class="col-security">{sec_disp}</td>'
            f'<td data-val="{usage_raw}" class="col-usage">{usage_disp}</td>'
            f'</tr>'
        )

    tbody = "\n".join(row_lines)

    checkboxes = "\n      ".join(
        f'<label><input type="checkbox" class="stab-cb" value="{level}" checked>'
        f' {_STABILITY_META[level][0]}</label>'
        for level in STABILITY_ORDER
    )

    security_checkboxes = "\n      ".join(
        f'<label><input type="checkbox" class="sec-cb" value="{status}" checked>'
        f' {_SECURITY_META[status][0]}</label>'
        for status in SECURITY_ORDER
    )

    return (
        '<!DOCTYPE html>\n'
        '<html lang="en">\n'
        '<head>\n'
        '  <meta charset="UTF-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        '  <title>Drupal AI Dependents</title>\n'
        f'  <style>\n{_CSS}\n  </style>\n'
        '</head>\n'
        '<body>\n'
        '  <h1>Drupal Modules &mdash; '
        '<a href="https://www.drupal.org/project/ai">drupal/ai</a> Dependents</h1>\n'
        f'  <p class="meta">Generated {today} &middot; {count} modules'
        f' &middot; Drupal {v_label} compatible</p>\n'
        '  <div class="controls">\n'
        '    <input id="filter" type="search" placeholder="Filter by module name&hellip;">\n'
        '    <span class="stab-filters">Show:\n'
        f'      {checkboxes}\n'
        '    </span>\n'
        '    <span class="stab-filters">Security:\n'
        f'      {security_checkboxes}\n'
        '    </span>\n'
        '  </div>\n'
        '  <table id="tbl">\n'
        '    <thead>\n'
        '      <tr>\n'
        '        <th data-col="0" data-type="text">Label</th>\n'
        '        <th data-col="1" data-type="text">machine name</th>\n'
        '        <th data-col="2" data-type="text">Version</th>\n'
        '        <th data-col="3" data-type="date">Released</th>\n'
        '        <th data-col="4" data-type="num" class="col-security">Security coverage</th>\n'
        '        <th data-col="5" data-type="num" class="col-usage">Drupal.org usage</th>\n'
        '      </tr>\n'
        '    </thead>\n'
        '    <tbody id="tbody">\n'
        f'{tbody}\n'
        '    </tbody>\n'
        '  </table>\n'
        '  <p id="no-results">No modules match your filter.</p>\n'
        f'  <script>\n{_JS}\n  </script>\n'
        '</body>\n'
        '</html>\n'
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("json_file", metavar="FILE",
                        help="Path to results.json from drupal_ai_dependents.py")
    parser.add_argument("--output", "-o", metavar="OUT", required=True,
                        help="Write HTML to this file")
    args = parser.parse_args()

    with open(args.json_file, encoding="utf-8") as fh:
        payload = json.load(fh)

    html_out = render_html(payload)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(html_out)
    print(f"HTML written to {args.output} ({len(payload['modules'])} modules)", file=sys.stderr)


if __name__ == "__main__":
    main()
