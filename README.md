# Drupal AI Dependents

Generates a markdown table of all Drupal modules that declare a **hard dependency on [drupal/ai](https://www.drupal.org/project/ai)** in their `composer.json`, showing each module's name, URL, latest release version, release date, and active install count.

## Requirements

- Python 3.10 or later
- No third-party packages — uses only Python standard library (`urllib`, `xml.etree.ElementTree`, `argparse`, etc.)

## Usage

### Recommended workflow — collect once, render as many times as you like

```bash
# Run the slow network fetch (~45-60 min) and save to JSON
python3 drupal_ai_dependents.py --json results.json

# Re-render from the saved JSON (fast — no network calls)
python3 render_md.py results.json -o results.md
python3 render_html.py results.json -o results.html
```

### All-in-one — collect and render in a single run

```bash
# Print markdown table to stdout
python3 drupal_ai_dependents.py

# Write markdown to a file (progress still prints to terminal)
python3 drupal_ai_dependents.py -o results.md

# Save JSON + markdown + HTML in one run
python3 drupal_ai_dependents.py --json results.json -o results.md --html results.html
```

## Output

### JSON

The `--json FILE` flag writes a structured JSON file that can be re-rendered without re-fetching any data:

```json
{
  "generated": "2026-06-22",
  "drupal_versions": [10, 11],
  "modules": [
    {
      "machine_name": "ai_provider_openai",
      "name": "Ai Provider Openai",
      "url": "https://www.drupal.org/project/ai_provider_openai",
      "version": "1.2.1",
      "release_date": "2026-02-25",
      "usage": 13133,
      "stability": "stable"
    }
  ]
}
```

The `stability` field is derived from the version string: `stable`, `rc`, `beta`, `alpha`, or `dev`.

### Markdown

A markdown table sorted by active installs (descending), for example:

| Module | URL | Latest Version | Release Date | Usage (installs) |
|--------|-----|:--------------:|:------------:|----------------:|
| Ai Provider Openai | https://www.drupal.org/project/ai_provider_openai | `1.2.1` | 2026-02-25 | 10,508 |
| Ai Image Alt Text | https://www.drupal.org/project/ai_image_alt_text | `1.0.2` | 2025-12-05 | 8,894 |

Modules with no tracked install count show `—` in the usage column.

### HTML

`render_html.py results.json -o results.html` generates a self-contained HTML file with no external dependencies (all CSS and JavaScript is embedded). The table supports:

- **Sort by any column** — click a column header to sort ascending; click again to sort descending. Sort direction is indicated by ▲/▼ in the header.
- **Filter by module name** — type in the search box to instantly hide non-matching rows.
- **Filter by stability level** — checkboxes let you show or hide stable, RC, beta, alpha, and dev releases independently. Each version cell displays a small colored badge indicating stability.

Default sort is by install count (descending), matching the markdown output order.

The `--html FILE` flag on the main script still works as a convenience shortcut, but produces the older HTML output without stability badges or checkboxes.

## How it works

### Stage 1 — Candidate discovery (two sources, union-merged)

**Source 1:** Paginates through all pages of `drupal.org/project/ai/ecosystem` — the AI module's curated ecosystem listing — to collect project machine names.

**Source 2:** Paginates through `packages.drupal.org/8/search.json?s=ai` — a full-text search of the Drupal Composer repository — to collect additional candidate package names. This catches modules that depend on drupal/ai but have not been added to the ecosystem listing.

Both sources are merged and deduplicated (~730 unique candidates on a typical run).

### Stage 2 — Dependency verification (authoritative)

For each candidate, the script fetches the module's Composer v2 metadata file from:

```
packages.drupal.org/files/packages/8/p2/drupal/{name}.json
```

This file contains the parsed contents of each version's `composer.json`. The script checks:

1. `type == "drupal-module"` — excludes recipes, profiles, and distributions
2. `"drupal/ai"` is present in the `require` field — confirms a hard dependency
3. The `drupal/core` constraint includes Drupal 10 or 11

Only modules passing all three checks are included. This makes the list **more precise** than an ecosystem-page approach: modules that appear on the ecosystem page but don't actually require drupal/ai (e.g. companion modules, integrations) are correctly excluded.

The p2 file also provides:
- The exact version string
- The release datestamp (`extra.drupal.datestamp`), used for the release date column

### Stage 3 — Additional data

- **Release date fallback:** If the p2 file has no datestamp, `updates.drupal.org/release-history/{name}/current` is queried.
- **Usage/install count:** `www.drupal.org/api-d7/node.json` is queried for the `project_usage` field, which reports active installs across all tracked versions.

## Rate limiting

All delays are defined near the top of `drupal_ai_dependents.py` in the **Configuration** section (around line 43):

```python
# Polite delays — applied BEFORE each request
ECOSYSTEM_PAGE_DELAY = 3.0   # www.drupal.org ecosystem pages
PACKAGES_DELAY       = 3.0   # packages.drupal.org (search pages + p2 files)
RELEASE_DELAY        = 3.0   # updates.drupal.org (date fallback only)
DRUPAL_API_DELAY     = 3.0   # www.drupal.org JSON API
```

With ~730 candidates to verify and 3-second delays before every request, a full run takes approximately **45–60 minutes**. Do not reduce these values significantly — the Drupal.org JSON API will return `503 Service Unavailable` responses if called too frequently.

If a `503` is received, the script automatically retries up to 4 times, honouring the server's `Retry-After` response header if present, or falling back to exponential backoff starting at 2 seconds.

## Terminal feedback

Progress is printed to `stderr` throughout the run:

```
Collecting candidates …
  [1/2] Scraping AI ecosystem pages …
    Page 0 …
    …
    Page 10 …
        268 names found
  [2/2] Searching packages.drupal.org for 'ai' …
        649 names found
  733 unique candidates after merge

Verifying via packages.drupal.org p2 files …
  [1/733] issues …
    → skipped
  [2/733] ai_provider_openai …
  [3/733] ai_agents …
  …

Results: 182 confirmed modules (551 skipped, 3 used date fallback)
```

Because progress goes to `stderr` and the markdown table goes to `stdout`, they do not interfere when redirecting output to a file.

## Limitations

**Coverage depends on candidate sources.** The script checks ~730 candidates drawn from the ecosystem page and a package name search for "ai". A module that hard-depends on drupal/ai but has no connection to "ai" in its name or description, and has not been added to the AI ecosystem listing, will be missed. There is no reverse-dependency index in the Composer protocol that would enable exhaustive enumeration of all 18,000+ packages on packages.drupal.org.

**The dependency check uses the latest version only.** If a module's newest release dropped the drupal/ai dependency but an older release had it, the module will not appear in the list. Conversely, if only a dev release has the dependency, that release will be included (all stability levels are checked).

**Install counts are approximate.** The `project_usage` field from the Drupal.org API reports sites that have submitted an update check recently. It undercounts sites with update checking disabled, and may overcount sites reporting from multiple environments.

**Install counts are absent for newer/less-adopted modules.** Modules with no tracked installs show `—`. This typically means the module is very new, has very low adoption, or is a dev-only tool.

**Results reflect the data sources at the time of the run.** The ecosystem page, package search index, and p2 files all change as new modules are published. Re-run the script periodically for an up-to-date snapshot.

**Python 3.10+ required.** The script uses union type hints (`X | Y`) introduced in Python 3.10.
