# Drupal AI Dependents

Generates a markdown table of all Drupal modules that declare a **hard dependency on [drupal/ai](https://www.drupal.org/project/ai)** in their `composer.json`, showing each module's label, machine name, URL, latest release version, release date, security advisory coverage, and active install count.

## Requirements

- Python 3.10 or later
- No third-party packages — uses only Python standard library (`urllib`, `xml.etree.ElementTree`, `argparse`, etc.)

## Usage

### Recommended workflow — collect once, render as many times as you like

```bash
# Run the network fetch (~17-20 min) and save to JSON
python3 drupal_ai_dependents.py --json results.json

# Re-render from the saved JSON (fast — no network calls)
python3 render_md.py results.json -o results.md
python3 render_html.py results.json -o index.html
```

### All-in-one — collect and render in a single run

```bash
# Print markdown table to stdout
python3 drupal_ai_dependents.py

# Write markdown to a file (progress still prints to terminal)
python3 drupal_ai_dependents.py -o results.md

# Save JSON + markdown + HTML in one run
python3 drupal_ai_dependents.py --json results.json -o results.md --html index.html
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
      "machine_name": "drupal/ai_provider_openai",
      "label": "OpenAI Provider",
      "url": "https://www.drupal.org/project/ai_provider_openai",
      "version": "1.2.1",
      "release_date": "2026-02-25",
      "usage": 13133,
      "security_covered": true,
      "stability": "stable"
    }
  ]
}
```

- `machine_name` is the `name` field from the module's `composer.json` (e.g. `drupal/ai_provider_openai`), not just the bare project machine name.
- `label` is the `name` key from the module's `*.info.yml` file (its actual display title), fetched from `git.drupalcode.org`. Falls back to a title-cased version of the machine name if the file can't be fetched.
- `security_covered` reflects whether the project is covered by Drupal's security advisory policy (`field_security_advisory_coverage == "covered"` on drupal.org).
- `stability` is derived from the version string: `stable`, `rc`, `beta`, `alpha`, or `dev`.

### Markdown

A markdown table sorted by active installs (descending), for example:

| Label | machine name | URL | Latest Version | Release Date | Security coverage | Drupal.org usage |
|-------|--------------|-----|:--------------:|:------------:|:------------------:|----------------:|
| OpenAI Provider | drupal/ai_provider_openai | https://www.drupal.org/project/ai_provider_openai | `1.2.1` | 2026-02-25 | ✅ | 10,508 |
| AI Image Alt Text | drupal/ai_image_alt_text | https://www.drupal.org/project/ai_image_alt_text | `1.0.2` | 2025-12-05 | 🚫 | 8,894 |

The Label column links to the module's project page. Modules with no tracked install count show `—` in the usage column. Security coverage shows ✅ when covered by Drupal's security advisory policy, 🚫 otherwise.

### HTML

`render_html.py results.json -o index.html` generates a self-contained HTML file with no external dependencies (all CSS and JavaScript is embedded). The table supports:

- **Sort by any column** — click a column header to sort ascending; click again to sort descending. Sort direction is indicated by ▲/▼ in the header.
- **Filter by module name** — type in the search box to instantly hide non-matching rows.
- **Filter by stability level** — checkboxes let you show or hide stable, RC, beta, alpha, and dev releases independently. Each version cell displays a small colored badge indicating stability.
- **Filter by security coverage** — checkboxes let you show or hide modules that are covered vs. not covered by Drupal's security advisory policy.

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
- **Module label:** `git.drupalcode.org/project/{name}/-/raw/{version}/{name}.info.yml` is fetched and its `name:` key is read — this is the module's actual display title, distinct from both the machine name and the composer package name.
- **Usage/install count and security coverage:** `www.drupal.org/api-d7/node.json` is queried once per module for both the `project_usage` field (summed across all tracked versions) and `field_security_advisory_coverage` (`"covered"` or `"not-covered"`).

## Rate limiting

All delays are defined near the top of `drupal_ai_dependents.py` in the **Configuration** section (around line 43):

```python
# Polite delays — applied BEFORE each request
ECOSYSTEM_PAGE_DELAY = 3.0   # www.drupal.org ecosystem pages
PACKAGES_DELAY       = 3.0   # packages.drupal.org (search pages + p2 files)
RELEASE_DELAY        = 3.0   # updates.drupal.org (date fallback only)
DRUPAL_API_DELAY     = 3.0   # www.drupal.org JSON API
GITLAB_DELAY         = 3.0   # git.drupalcode.org (info.yml label fetch)
```

The p2 verification and info.yml label phases each run 3 concurrent workers, every worker sleeping its phase's delay before its own request. Both packages.drupal.org and git.drupalcode.org are CDN-backed static file servers and handle this rate comfortably. The drupal.org JSON API (usage counts + security coverage) remains sequential at `DRUPAL_API_DELAY` — do not reduce that value, as it returned `503 Service Unavailable` during development when called faster than ~1 per second.

A full run takes approximately **17–20 minutes**.

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
  [47/733] issues: skip
  [51/733] ai_provider_openai: ok
  [89/733] ai_agents: ok
  …

Fetching module labels from info.yml files …
  [1/182] ai_provider_openai: OpenAI Provider
  [2/182] ai_agents: AI Agents
  …

Fetching usage counts …
  [1/182] ai_provider_openai: usage=13133 security_covered=True
  [2/182] ai_agents: usage=10758 security_covered=True
  …

Results: 182 confirmed modules (551 skipped, 3 used date fallback)
```

The p2 verification and info.yml label phases each complete candidates out-of-order (3 workers run concurrently), so progress numbers are not sequential within those phases. The usage-count phase runs in order. Every module prints a line in each phase — this matters for the usage-count phase in particular, since it's fully sequential at 3s/module and can otherwise look stalled for many minutes.

Because progress goes to `stderr` and the markdown table goes to `stdout`, they do not interfere when redirecting output to a file.

## Limitations

**Coverage depends on candidate sources.** The script checks ~730 candidates drawn from the ecosystem page and a package name search for "ai". A module that hard-depends on drupal/ai but has no connection to "ai" in its name or description, and has not been added to the AI ecosystem listing, will be missed. There is no reverse-dependency index in the Composer protocol that would enable exhaustive enumeration of all 18,000+ packages on packages.drupal.org.

**The latest stable release is preferred.** The script picks the newest stable release that passes all checks. If no stable release exists it falls back to the newest pre-release (rc, beta, alpha, dev). If a module's latest stable release dropped the drupal/ai dependency, the module will not appear even if a dev release still has it.

**Install counts are approximate.** The `project_usage` field from the Drupal.org API reports sites that have submitted an update check recently. It undercounts sites with update checking disabled, and may overcount sites reporting from multiple environments.

**Install counts are absent for newer/less-adopted modules.** Modules with no tracked installs show `—`. This typically means the module is very new, has very low adoption, or is a dev-only tool.

**Results reflect the data sources at the time of the run.** The ecosystem page, package search index, and p2 files all change as new modules are published. Re-run the script periodically for an up-to-date snapshot.

**Python 3.10+ required.** The script uses union type hints (`X | Y`) introduced in Python 3.10.
