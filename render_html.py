#!/usr/bin/env python3
"""
Render a self-contained HTML table from results.json produced by drupal_ai_dependents.py.

Features: a Modules/Recipes tabbed view, sortable columns, name filter,
stability-level checkboxes (stable/rc/beta/alpha/dev) and security coverage
checkboxes (covered/not covered) on the Modules tab — recipes have neither
usage tracking nor meaningful security-advisory data, so the Recipes tab
only offers sorting and a name filter.

Usage:
  python3 render_html.py results.json -o output.html
"""

import argparse
import html
import json
import sys


STABILITY_ORDER = ["stable", "rc", "beta", "alpha", "dev"]

# Security advisory coverage icons.
# Filled shield — covered + stable release — Drupal.org's own SVG.
SECURITY_COVERED_STABLE_HTML = (
    '<img src="images/shield-icon-black.svg" width="16" height="16" style="opacity:0.5" '
    'alt="Security covered" title="Security covered (stable release)">'
)
# Outline shield — covered + pre-release — inline SVG, stroke only, no fill.
SECURITY_COVERED_PRERELEASE_HTML = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
    'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" '
    'stroke-linejoin="round" style="opacity:0.5" '
    'aria-label="Security covered (pre-release)" '
    'title="Security covered (pre-release)">'
    '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>'
    '</svg>'
)
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

# (display label, icon) keyed by the same value stored in data-security.
# The "Covered" checkbox uses the filled-shield icon (stable) as its label.
_SECURITY_META = {
    "covered":     ("Covered", SECURITY_COVERED_STABLE_HTML),
    "not-covered": ("Not covered", SECURITY_NOT_COVERED_EMOJI),
}

# Category filter ordering. Mirrors CATEGORIES in drupal_ai_dependents.py; any
# category not listed here (a future addition, or "Uncategorized") still
# renders — it just sorts to the end. Kept local so this renderer runs on its
# own, like STABILITY_ORDER / SECURITY_ORDER above.
CATEGORY_ORDER = [
    "Tool", "Cloud Providers", "Local Providers", "Editorial", "Content",
    "Search", "Chat", "Automation", "Agents", "Analytics", "Media",
    "Vector Database", "SEO & Metadata", "Translation", "Safety & Governance",
    "Accessibility", "Developer Tools", "Evaluation & Testing",
]


def _ordered_categories(rows: list) -> list:
    """Categories present across rows, in CATEGORY_ORDER then any extras alpha."""
    present: set = set()
    for r in rows:
        present.update(r.get("categories", []))
    return ([c for c in CATEGORY_ORDER if c in present]
            + sorted(c for c in present if c not in CATEGORY_ORDER))


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
    .desc {
      color: #666;
      font-size: 0.8rem;
      font-weight: normal;
      line-height: 1.35;
      margin-top: 0.2rem;
      max-width: 44rem;
    }
    .col-version  { font-family: ui-monospace, monospace; white-space: nowrap; }
    .col-date     { white-space: nowrap; }
    .col-security { text-align: center; }
    th.col-security { text-align: center; }
    .col-usage        { text-align: right; }
    th.col-usage      { text-align: right; }
    .col-downloads    { text-align: right; }
    th.col-downloads  { text-align: right; }
    .col-stars        { text-align: right; }
    th.col-stars      { text-align: right; }
    .col-cat { white-space: normal; }
    .cat-pill {
      display: inline-block;
      padding: 1px 7px;
      margin: 1px 3px 1px 0;
      border-radius: 10px;
      font-size: 0.72rem;
      background: #e7eef5;
      color: #245a8c;
      white-space: nowrap;
    }
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
    .stab-dev    { background: #e2e3e5; color: #383d41; }
    .tabs {
      display: flex;
      gap: 0.5rem;
      margin-bottom: 1rem;
      border-bottom: 2px solid #ddd;
    }
    .tab-btn {
      padding: 0.5rem 1rem;
      border: none;
      background: none;
      font-size: 1rem;
      cursor: pointer;
      color: #555;
      border-bottom: 2px solid transparent;
      margin-bottom: -2px;
    }
    .tab-btn.active {
      color: #2d6a9f;
      border-bottom-color: #2d6a9f;
      font-weight: 600;
    }
    .tab-panel { display: none; }
    .tab-panel.active { display: block; }
    .legend {
      font-size: 0.8rem;
      color: #555;
      margin-top: 0.5rem;
      display: flex;
      align-items: center;
      gap: 0.35rem;
    }
    .chosen-container { font-size: 0.9rem; }
    .chosen-container-multi .chosen-choices {
      border: 1px solid #bbb;
      border-radius: 4px;
      background: #fff;
      min-width: 280px;
    }"""

# JS stored as a plain string (not an f-string) to avoid escaping every { }.
_JS = """\
    (function () {
      // Column-sort mechanics are identical for both tables, so this factory
      // is shared. Filtering is NOT shared — the modules table filters by
      // name + stability + security, the recipes table only by name, and
      // forcing the simpler recipes filter through the modules' shape would
      // add a pointless conditional to already-tested code.
      function makeSorter(theadSel, tbodySel) {
        var thead = document.querySelector(theadSel);
        var tbody = document.querySelector(tbodySel);
        var sortCol = -1, sortDir = 1;

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

        return { sortTable: sortTable, cellVal: cellVal, tbody: tbody };
      }

      // ---- Modules tab: sorting + name/stability/security filter ----
      var modulesSorter = makeSorter('#tbl-modules thead tr', '#tbody-modules');
      modulesSorter.sortTable(4, 'num');

      var filterModules = document.getElementById('filter-modules');
      var noResModules   = document.getElementById('no-results-modules');

      var catModules = document.getElementById('cat-modules');

      function applyModulesFilter() {
        var q = filterModules.value.toLowerCase();
        var stabChecked = new Set(
          Array.from(document.querySelectorAll('.stab-cb:checked'))
               .map(function (cb) { return cb.value; })
        );
        var secChecked = new Set(
          Array.from(document.querySelectorAll('.sec-cb:checked'))
               .map(function (cb) { return cb.value; })
        );
        var catSelected = catModules ? Array.from(catModules.selectedOptions).map(function (o) { return o.value; }) : [];
        var visible = 0;
        Array.from(modulesSorter.tbody.querySelectorAll('tr')).forEach(function (row) {
          var nameMatch = modulesSorter.cellVal(row, 0).toLowerCase().indexOf(q) !== -1;
          var stabMatch = stabChecked.has(row.dataset.stability);
          var secMatch = secChecked.has(row.dataset.security);
          var cats = (row.dataset.categories || '').split('|').filter(Boolean);
          var catMatch = catSelected.length === 0 || cats.length === 0 || cats.some(function (c) { return catSelected.indexOf(c) !== -1; });
          var show = nameMatch && stabMatch && secMatch && catMatch;
          row.style.display = show ? '' : 'none';
          if (show) visible++;
        });
        noResModules.style.display = (visible === 0) ? '' : 'none';
      }

      filterModules.addEventListener('input', applyModulesFilter);
      Array.from(document.querySelectorAll('.stab-cb, .sec-cb')).forEach(function (cb) {
        cb.addEventListener('change', applyModulesFilter);
      });
      if (catModules) catModules.addEventListener('change', applyModulesFilter);

      // ---- Recipes tab: sorting + name-only filter ----
      // No stability or security data exists for recipes, so there's
      // nothing to check here beyond the name filter. Sort by Packagist
      // downloads descending on load (column 3, numeric) — same pattern
      // as modules' usage sort.
      var recipesSorter = makeSorter('#tbl-recipes thead tr', '#tbody-recipes');
      recipesSorter.sortTable(3, 'num');

      var filterRecipes = document.getElementById('filter-recipes');
      var noResRecipes   = document.getElementById('no-results-recipes');

      var catRecipes = document.getElementById('cat-recipes');

      function applyRecipesFilter() {
        var q = filterRecipes.value.toLowerCase();
        var catSelected = catRecipes ? Array.from(catRecipes.selectedOptions).map(function (o) { return o.value; }) : [];
        var visible = 0;
        Array.from(recipesSorter.tbody.querySelectorAll('tr')).forEach(function (row) {
          var nameMatch = recipesSorter.cellVal(row, 0).toLowerCase().indexOf(q) !== -1;
          var cats = (row.dataset.categories || '').split('|').filter(Boolean);
          var catMatch = catSelected.length === 0 || cats.length === 0 || cats.some(function (c) { return catSelected.indexOf(c) !== -1; });
          var show = nameMatch && catMatch;
          row.style.display = show ? '' : 'none';
          if (show) visible++;
        });
        noResRecipes.style.display = (visible === 0) ? '' : 'none';
      }

      filterRecipes.addEventListener('input', applyRecipesFilter);
      if (catRecipes) catRecipes.addEventListener('change', applyRecipesFilter);

      // ---- Tab switching ----
      Array.from(document.querySelectorAll('.tab-btn')).forEach(function (btn) {
        btn.addEventListener('click', function () {
          Array.from(document.querySelectorAll('.tab-btn')).forEach(function (b) { b.classList.remove('active'); });
          Array.from(document.querySelectorAll('.tab-panel')).forEach(function (p) { p.classList.remove('active'); });
          btn.classList.add('active');
          document.getElementById('panel-' + btn.dataset.tab).classList.add('active');
        });
      });

      // ---- Chosen.js init ----
      if (typeof jQuery !== 'undefined') {
        jQuery('.chosen-select').chosen({ width: '100%', search_contains: true });
        jQuery('.chosen-select').on('change', function () {
          if (this.id === 'cat-modules') applyModulesFilter();
          else if (this.id === 'cat-recipes') applyRecipesFilter();
        });
      }
    })();"""


def render_html(payload: dict) -> str:
    """Return a self-contained HTML string with a tabbed Modules/Recipes view."""
    rows         = payload["modules"]
    recipe_rows  = payload.get("recipes", [])  # back-compat: older files have no "recipes" key
    today        = payload["generated"]
    v_label      = "/".join(str(v) for v in sorted(payload["drupal_versions"]))
    count        = len(rows)
    recipe_count = len(recipe_rows)

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
        if sec_covered:
            sec_disp = (SECURITY_COVERED_STABLE_HTML
                        if stability == "stable"
                        else SECURITY_COVERED_PRERELEASE_HTML)
        else:
            sec_disp = SECURITY_NOT_COVERED_EMOJI
        sec_val      = "1" if sec_covered else "0"
        sec_status   = "covered" if sec_covered else "not-covered"
        stab_label, css_class = _STABILITY_META.get(stability, ("Stable", "stab-stable"))
        badge = f'<span class="badge {css_class}">{stab_label}</span>'
        desc = r.get("description")
        desc_html = f'<div class="desc">{esc(desc)}</div>' if desc else ''
        cats     = r.get("categories", [])
        cat_val  = esc(" ".join(cats))                       # sort key
        cat_data = esc("|".join(cats))                       # filter (data-categories)
        cat_html = "".join(f'<span class="cat-pill">{esc(c)}</span>' for c in cats)
        row_lines.append(
            f'      <tr data-stability="{esc(stability)}" data-security="{sec_status}" data-categories="{cat_data}">'
            f'<td data-val="{label_esc}"><a href="{url_esc}" title="{machine_esc}">{label_esc}</a>{desc_html}</td>'
            f'<td data-val="{ver_val}" class="col-version">{ver_disp}{badge}</td>'
            f'<td data-val="{date_val}" class="col-date">{esc(date)}</td>'
            f'<td data-val="{sec_val}" class="col-security">{sec_disp}</td>'
            f'<td data-val="{usage_raw}" class="col-usage">{usage_disp}</td>'
            f'<td data-val="{cat_val}" class="col-cat">{cat_html}</td>'
            f'</tr>'
        )

    tbody = "\n".join(row_lines)

    # Recipes have no usage tracking and no meaningful security-advisory
    # status, so these rows omit both the badge and the security cell
    # entirely rather than inventing an "N/A" state.
    recipe_row_lines = []
    for r in recipe_rows:
        label_esc   = esc(r["label"])
        machine_esc = esc(r["machine_name"])
        url_esc     = esc(r["url"])
        ver_raw     = r["version"]
        ver_disp    = esc(ver_raw) if ver_raw else "—"
        ver_val     = esc(ver_raw) if ver_raw else ""
        date        = r["release_date"]
        date_val    = "" if date == "—" else esc(date)
        downloads   = r.get("downloads")
        dl_raw      = str(downloads) if downloads is not None else ""
        dl_disp     = f"{downloads:,}" if downloads is not None else "—"
        stars       = r.get("stars")
        stars_raw   = str(stars) if stars is not None else ""
        stars_disp  = f"{stars:,}" if stars is not None else "—"
        desc        = r.get("description")
        desc_html   = f'<div class="desc">{esc(desc)}</div>' if desc else ''
        cats        = r.get("categories", [])
        cat_val     = esc(" ".join(cats))
        cat_data    = esc("|".join(cats))
        cat_html    = "".join(f'<span class="cat-pill">{esc(c)}</span>' for c in cats)
        recipe_row_lines.append(
            f'      <tr data-categories="{cat_data}">'
            f'<td data-val="{label_esc}"><a href="{url_esc}" title="{machine_esc}">{label_esc}</a>{desc_html}</td>'
            f'<td data-val="{ver_val}" class="col-version">{ver_disp}</td>'
            f'<td data-val="{date_val}" class="col-date">{esc(date)}</td>'
            f'<td data-val="{dl_raw}" class="col-downloads">{dl_disp}</td>'
            f'<td data-val="{stars_raw}" class="col-stars">{stars_disp}</td>'
            f'<td data-val="{cat_val}" class="col-cat">{cat_html}</td>'
            f'</tr>'
        )

    recipe_tbody = "\n".join(recipe_row_lines)

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

    # Category multi-select dropdowns (enhanced by Chosen.js), one per tab.
    def _cat_select(cats: list, select_id: str) -> str:
        options = "\n        ".join(
            f'<option value="{esc(c)}">{esc(c)}</option>'
            for c in cats
        )
        return (f'<select id="{select_id}" multiple class="chosen-select"'
                f' data-placeholder="Filter by category…">\n'
                f'        {options}\n'
                f'      </select>')
    module_cat_select = _cat_select(_ordered_categories(rows), "cat-modules")
    recipe_cat_select = _cat_select(_ordered_categories(recipe_rows), "cat-recipes")

    return (
        '<!DOCTYPE html>\n'
        '<html lang="en">\n'
        '<head>\n'
        '  <meta charset="UTF-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        '  <title>Drupal AI Dependents</title>\n'
        '  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/chosen/1.8.7/chosen.min.css">\n'
        f'  <style>\n{_CSS}\n  </style>\n'
        '</head>\n'
        '<body>\n'
        '  <h1>Drupal Modules &amp; Recipes &mdash; '
        '<a href="https://www.drupal.org/project/ai">drupal/ai</a> Dependents</h1>\n'
        f'  <p class="meta">Generated {today} &middot; {count} modules'
        f' &middot; {recipe_count} recipes &middot; Drupal {v_label} compatible</p>\n'
        '  <div class="tabs">\n'
        f'    <button class="tab-btn active" data-tab="modules">Modules ({count})</button>\n'
        f'    <button class="tab-btn" data-tab="recipes">Recipes ({recipe_count})</button>\n'
        '  </div>\n'
        '  <div class="tab-panel active" id="panel-modules">\n'
        '    <div class="controls">\n'
        '      <input id="filter-modules" type="search" placeholder="Filter by module name&hellip;">\n'
        '      <span class="stab-filters">Show:\n'
        f'        {checkboxes}\n'
        '      </span>\n'
        '      <span class="stab-filters">Security:\n'
        f'        {security_checkboxes}\n'
        '      </span>\n'
        f'      {module_cat_select}\n'
        '    </div>\n'
        '    <table id="tbl-modules">\n'
        '      <thead>\n'
        '        <tr>\n'
        '          <th data-col="0" data-type="text">Label</th>\n'
        '          <th data-col="1" data-type="text">Version</th>\n'
        '          <th data-col="2" data-type="date">Released</th>\n'
        '          <th data-col="3" data-type="num" class="col-security" title="Security Coverage">Security</th>\n'
        '          <th data-col="4" data-type="num" class="col-usage" title="Drupal.org Usage">Usage</th>\n'
        '          <th data-col="5" data-type="text" class="col-cat">Categories</th>\n'
        '        </tr>\n'
        '      </thead>\n'
        '      <tbody id="tbody-modules">\n'
        f'{tbody}\n'
        '      </tbody>\n'
        '    </table>\n'
        '    <p class="legend">Security coverage:\n'
        f'      {SECURITY_COVERED_STABLE_HTML} Covered (stable) &nbsp;&middot;&nbsp;\n'
        f'      {SECURITY_COVERED_PRERELEASE_HTML} Covered (pre-release) &nbsp;&middot;&nbsp;\n'
        f'      {SECURITY_NOT_COVERED_EMOJI} Not covered by security advisory policy\n'
        '    </p>\n'
        '    <p id="no-results-modules">No modules match your filter.</p>\n'
        '  </div>\n'
        '  <div class="tab-panel" id="panel-recipes">\n'
        '    <div class="controls">\n'
        '      <input id="filter-recipes" type="search" placeholder="Filter by recipe name&hellip;">\n'
        f'      {recipe_cat_select}\n'
        '    </div>\n'
        '    <table id="tbl-recipes">\n'
        '      <thead>\n'
        '        <tr>\n'
        '          <th data-col="0" data-type="text">Label</th>\n'
        '          <th data-col="1" data-type="text">Version</th>\n'
        '          <th data-col="2" data-type="date">Released</th>\n'
        '          <th data-col="3" data-type="num" class="col-downloads">Packagist downloads</th>\n'
        '          <th data-col="4" data-type="num" class="col-stars">Packagist stars</th>\n'
        '          <th data-col="5" data-type="text" class="col-cat">Categories</th>\n'
        '        </tr>\n'
        '      </thead>\n'
        '      <tbody id="tbody-recipes">\n'
        f'{recipe_tbody}\n'
        '      </tbody>\n'
        '    </table>\n'
        '    <p id="no-results-recipes">No recipes match your filter.</p>\n'
        '  </div>\n'
        '  <script src="https://cdnjs.cloudflare.com/ajax/libs/jquery/3.7.1/jquery.min.js"></script>\n'
        '  <script src="https://cdnjs.cloudflare.com/ajax/libs/chosen/1.8.7/chosen.jquery.min.js"></script>\n'
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
    module_count = len(payload['modules'])
    recipe_count = len(payload.get('recipes', []))
    print(f"HTML written to {args.output} ({module_count} modules, {recipe_count} recipes)", file=sys.stderr)


if __name__ == "__main__":
    main()
