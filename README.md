# Drupal AI Dependents

`drupal_ai_dependents.py` finds all Drupal modules and recipes that declare a **hard dependency on [drupal/ai](https://www.drupal.org/project/ai)** in their `composer.json`, and writes the results as JSON. It only collects and verifies data — `render_md.py` and `render_html.py` turn that JSON into a markdown file or a self-contained HTML page, each with two tables: Modules (label, machine name, URL, latest release version, release date, security advisory coverage, active install count) and Recipes (same first four columns plus Packagist download count and star count — no Drupal.org security coverage or usage, since those don't exist for recipes, see [Recipes](#recipes) below). Each project's own description (from its `*.info.yml` / `recipe.yml` `description:` key) is shown as a muted second line beneath its label in both tables.

## Requirements

- Python 3.10 or later
- No third-party packages — uses only Python standard library (`urllib`, `xml.etree.ElementTree`, `argparse`, etc.)
- **Optional:** a `git.drupalcode.org` API token to enable the GitLab composer.json content search (candidate discovery Source 3). Provide it via the `DRUPALCODE_TOKEN` or `GITLAB_TOKEN` environment variable, or by logging in with the [glab CLI](https://gitlab.com/gitlab-org/cli) (`glab auth login --hostname git.drupalcode.org`) — the script reads the token from glab's config automatically. Without a token this one source is **skipped with a warning** and the run proceeds on the other two sources exactly as before (see [How it works](#how-it-works)).

## Usage

```bash
# Run the network fetch (~25-30 min with a GitLab token, ~20-25 without) and save to JSON
python3 drupal_ai_dependents.py --json results.json

# Render from the saved JSON (fast — no network calls, re-run anytime)
python3 render_md.py results.json -o results.md
python3 render_html.py results.json -o index.html
```

`drupal_ai_dependents.py` only ever writes JSON (to a file with `--json FILE`, or to stdout if `--json` is omitted) — it has no markdown or HTML rendering of its own. An earlier version did, via `-o`/`--output` and `--html` flags, but that inline rendering fell out of sync with `render_html.py` every time the real renderer gained a feature (recipes, stability badges, filter checkboxes never made it into the inline version). Those flags are gone; `render_md.py` and `render_html.py` are now the only renderers.

## Output

### JSON

`--json FILE` writes a structured JSON file that can be re-rendered without re-fetching any data:

```json
{
  "generated": "2026-06-22",
  "drupal_versions": [10, 11],
  "modules": [
    {
      "machine_name": "drupal/ai_provider_openai",
      "label": "OpenAI Provider",
      "description": "Adds OpenAI as a provider for the AI module.",
      "url": "https://www.drupal.org/project/ai_provider_openai",
      "version": "1.2.1",
      "release_date": "2026-02-25",
      "usage": 13133,
      "security_covered": true,
      "stability": "stable"
    }
  ],
  "recipes": [
    {
      "machine_name": "drupal/ai_recipe_image_classification",
      "label": "AI Image Classification recipe",
      "description": "Configures automatic image classification using AI.",
      "url": "https://www.drupal.org/project/ai_recipe_image_classification",
      "version": "1.1.0",
      "release_date": "2026-02-12",
      "stability": "stable",
      "downloads": 1234,
      "stars": 56
    }
  ]
}
```

- `machine_name` is the `name` field from the project's `composer.json` (e.g. `drupal/ai_provider_openai`), not just the bare project machine name.
- `label` is the module's actual display title — the `name` key from its `*.info.yml` file (modules) or `recipe.yml` file (recipes), fetched from `git.drupalcode.org`. Falls back to a title-cased version of the machine name if the file can't be fetched.
- `description` is the `description:` key from its `*.info.yml` / `recipe.yml` file.
- `security_covered` reflects whether the project is covered by Drupal's security advisory policy (`field_security_advisory_coverage == "covered"` on drupal.org). **Recipe rows have no `security_covered` key at all** — not `null`, simply absent — since recipes have no meaningful security-advisory status for this purpose.
- `usage` is the summed `project_usage` install count. **Recipe rows have no `usage` key at all**, since Drupal.org's API has no usage-tracking data for recipes whatsoever (confirmed: the field is entirely absent from the API response, not zero).
- `downloads` and `stars` come from the same request to `packagist.org/packages/drupal/{name}.json` — `package.downloads.total` and `package.favers` respectively. (`favers` is Packagist's internal field name for its star count.) **Module rows have neither key** — modules live only on `packages.drupal.org`, not the main Packagist registry. Both are `null` if the stats call failed; `0` is a real zero (package exists but has no downloads/stars yet).
- `stability` is derived from the version string: `stable`, `rc`, `beta`, `alpha`, or `dev`.
- Recipe rows are sorted by `downloads` descending (`null`/0 last), then alphabetically by `label` for ties.

Older `results.json` files from before recipe support was added (no `"recipes"` key at all) are still readable — both renderers fall back to an empty list via `payload.get("recipes", [])`.

### Markdown

Two sections, each with their own table. Modules is sorted by active installs (descending):

| Label | machine name | URL | Latest Version | Release Date | Security coverage | Drupal.org usage |
|-------|--------------|-----|:--------------:|:------------:|:------------------:|----------------:|
| OpenAI Provider | drupal/ai_provider_openai | https://www.drupal.org/project/ai_provider_openai | `1.2.1` | 2026-02-25 | ✅ | 10,508 |
| AI Image Alt Text | drupal/ai_image_alt_text | https://www.drupal.org/project/ai_image_alt_text | `1.0.2` | 2025-12-05 | 🚫 | 8,894 |

The Label column links to the module's project page, with the project's description (when present) shown as a small second line beneath the link (`<br><sub>…</sub>`). Modules with no tracked install count show `—` in the usage column. Security coverage uses three states: a filled shield icon (`images/shield-icon-black.svg`) when covered and on a stable release, an outline shield icon when covered but still pre-release (rc/beta/alpha/dev), and 🚫 when not covered. Both shield icons are rendered as `<img>` tags in the Markdown output.

Recipes is sorted by Packagist downloads descending, with no Security coverage or Drupal.org usage columns:

| Label | machine name | URL | Latest Version | Release Date | Packagist downloads | Packagist stars |
|-------|--------------|-----|:--------------:|:------------:|--------------------:|----------------:|
| AI Image Classification recipe | drupal/ai_recipe_image_classification | https://www.drupal.org/project/ai_recipe_image_classification | `1.1.0` | 2026-02-12 | 1,234 | 56 |

### HTML

`render_html.py results.json -o index.html` generates a self-contained HTML file with no external dependencies (all CSS and JavaScript is embedded), with a **Modules / Recipes tabbed interface**. Both tabs support:

- **Sort by any column** — click a column header to sort ascending; click again to sort descending. Sort direction is indicated by ▲/▼ in the header.
- **Filter by name** — type in the search box to instantly hide non-matching rows.

The Modules tab additionally supports:

- **Filter by stability level** — checkboxes let you show or hide stable, RC, beta, alpha, and dev releases independently. Each version cell displays a small colored badge indicating stability.
- **Filter by security coverage** — checkboxes let you show or hide modules that are covered vs. not covered by Drupal's security advisory policy. The security cell uses three states: a filled shield (`images/shield-icon-black.svg`, 50% opacity) for covered stable releases, an outline shield (inline SVG, 50% opacity) for covered pre-releases, and 🚫 for not covered. Both shield variants belong to the "covered" filter state.

The Recipes tab has no stability or security checkboxes — neither concept applies to recipes. Modules default to sorting by install count (descending); Recipes default to sorting by Packagist downloads (descending).

## Recipes

Drupal **recipes** (project type `drupal-recipe`, applied via `drush recipe` rather than installed as a module) are tracked separately from modules because they genuinely have neither install-tracking nor a meaningful security-advisory status — not because the data is missing, but because it doesn't exist for that project type. Confirmed live: Drupal.org's API returns no `project_usage` field at all for a recipe (absent, not zero).

Recipes also live on a different package registry entirely: they're not on packages.drupal.org (the source for everything else in this script), only on the main Packagist registry (`packagist.org`/`repo.packagist.org`). Because they're on Packagist, their total download counts and star counts **are** available via `packagist.org/packages/drupal/{name}.json` and are shown in the Recipes table as substitute popularity signals. Both values come from the same API request. Note that downloads count Composer install events (cumulative total), not active sites like the module usage figures; stars count Packagist ★ events. See [How it works](#how-it-works) below for the discovery/verification details.

## How it works

### Stage 1 — Candidate discovery (three sources, union-merged)

**Source 1:** Paginates through all pages of `drupal.org/project/ai/ecosystem` — the AI module's curated ecosystem listing — to collect project machine names.

**Source 2:** Paginates through `packages.drupal.org/8/search.json?s=ai` — a full-text search of the Drupal Composer repository — to collect additional candidate package names. This catches modules that depend on drupal/ai but have not been added to the ecosystem listing.

**Source 3 (requires a token):** Queries `git.drupalcode.org/api/v4/groups/2/search?scope=blobs&search=drupal/ai path:composer.json` — a GitLab search of the **content** of every project's `composer.json` on Drupal.org. Unlike Sources 1 and 2, which match on names and descriptions, this finds dependents whose name/description never mention "ai" at all (e.g. `deepgram`, `elevenlabs`, `bulk_content_generation`) — the primary way the coverage gap in [Limitations](#limitations) is closed. The search returns project IDs, which are resolved to machine names via `git.drupalcode.org/api/v4/projects/{id}`. This endpoint needs authentication (HTTP 401 without a token); when no token is found the whole source is skipped with a warning. It's a deliberately noisy keyword match — false positives are filtered out by the same Stage 2 verification as the other sources.

All available sources are merged and deduplicated (~730+ unique candidates on a typical run). Resolved GitLab names that fail module verification are also fed into recipe discovery (Stage 1b).

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
- **Module label + description:** `git.drupalcode.org/project/{name}/-/raw/{version}/{name}.info.yml` is fetched and both its `name:` key (the module's actual display title, distinct from the machine name and composer package name) and its `description:` key (the maintainer-authored description) are read from that single request.
- **Usage/install count and security coverage:** `www.drupal.org/api-d7/node.json` is queried once per module for both the `project_usage` field (summed across all tracked versions) and `field_security_advisory_coverage` (`"covered"` or `"not-covered"`).

### Stage 1b — Recipe candidate discovery (four sources)

Recipes aren't on packages.drupal.org at all (confirmed: a 404 on the module p2 endpoint for any recipe), so they need their own discovery and verification path against a different registry.

**Source 1:** Ecosystem-page names from Stage 1 that did **not** verify as a module — reused for free, no new request. A name can't be both a confirmed module and a recipe.

**Source 2:** GitLab composer.json search names from Stage 1 (Source 3) that did **not** verify as a module — reused for free, same rationale as the ecosystem reuse. Catches recipes the GitLab content search surfaces (e.g. `drupal_cms_ai`, `dxpr_cms`).

**Source 3:** `packagist.org/search.json?type=drupal-recipe&q=ai` — the regular Packagist registry (a separate service from packages.drupal.org), filtered by recipe type and narrowed with the same `"ai"` keyword tradeoff already accepted for module search.

**Source 4:** A curated, hand-maintained list of AI recipes from the AI Dashboard module (`git.drupalcode.org/project/ai_dashboard/-/raw/1.0.x/ai_dashboard_recommended_recipes.yml`). Not authoritative — every name from it still goes through full verification — but it catches at least one real recipe (`drupal_cms_ai`) that the ecosystem page doesn't list at all.

### Stage 2b — Recipe verification (authoritative)

Each recipe candidate is verified against the **main Packagist registry** instead of packages.drupal.org:

```
repo.packagist.org/p2/drupal/{name}.json
repo.packagist.org/p2/drupal/{name}~dev.json
```

Same Composer v2 p2 format and the same three checks as module verification (type, `drupal/ai` in `require`, Drupal 10/11 core constraint) — only the required `type` differs (`drupal-recipe` instead of `drupal-module`). **Both URLs are fetched and merged**, because Composer splits stable/tagged releases from branch/dev snapshots into separate files; a recipe with only a `"1.x-dev"` release would otherwise appear to have no qualifying version at all.

Unlike packages.drupal.org's p2 files, Packagist's have a real ISO-8601 `time` field on every version, so no date-fallback request is ever needed for recipes.

**Recipe label + description:** `git.drupalcode.org/project/{name}/-/raw/{ref}/recipe.yml` is fetched and both its `name:` and `description:` keys are read (same single-fetch approach as modules). Recipes don't have a `{name}.info.yml` like modules — their title and description live in this fixed-filename `recipe.yml` instead. Composer's `"-dev"` version suffix isn't a real git ref (e.g. `"1.x-dev"` → branch `"1.x"`), so the version is converted before building this URL.

No Drupal.org usage or security-coverage call is ever made for recipes — that data doesn't exist for this project type (see [Recipes](#recipes) above).

### Stage 3b — Recipe download counts

For each verified recipe, the script fetches:

```
packagist.org/packages/drupal/{name}.json
```

Both `package.downloads.total` (downloads) and `package.favers` (stars) are extracted from the same response — no second request is needed for stars. This phase runs 3 concurrent workers (same `PACKAGIST_DELAY` as the other Packagist calls) after recipe label fetching.

## Rate limiting

All delays are defined near the top of `drupal_ai_dependents.py` in the **Configuration** section (around line 43):

```python
# Polite delays — applied BEFORE each request
ECOSYSTEM_PAGE_DELAY = 3.0   # www.drupal.org ecosystem pages
PACKAGES_DELAY       = 3.0   # packages.drupal.org (search pages + p2 files)
RELEASE_DELAY        = 3.0   # updates.drupal.org (date fallback only)
DRUPAL_API_DELAY     = 3.0   # www.drupal.org JSON API
GITLAB_DELAY         = 3.0   # git.drupalcode.org (info.yml / recipe.yml label fetch + blob search + project-id resolution)
PACKAGIST_DELAY      = 3.0   # packagist.org (recipe search) + repo.packagist.org (recipe p2)
```

The GitLab project-id resolution, p2 verification, info.yml label, recipe p2 verification, recipe label, and recipe download phases each run 3 concurrent workers (one phase at a time, not overlapping), every worker sleeping its phase's delay before its own request. (The GitLab blob search itself is a short ~4-page sequential loop that runs first.) packages.drupal.org, git.drupalcode.org, and (assumed, not separately stress-tested) packagist.org/repo.packagist.org are all CDN-backed static file/JSON servers and handle this rate comfortably; git.drupalcode.org's GitLab REST API (blob search + project resolution) returned no throttling at this rate live. The drupal.org JSON API (usage counts + security coverage) remains sequential at `DRUPAL_API_DELAY` — do not reduce that value, as it returned `503 Service Unavailable` during development when called faster than ~1 per second. It's only ever called for modules, never recipes.

A full run takes approximately **25–30 minutes** when the GitLab source runs (its ~366-project name-resolution phase adds ~6 minutes), or **20–25 minutes** without a token. Recipe verification adds roughly 3-5 minutes on top of the module pipeline.

If a `503` is received, the script automatically retries up to 4 times, honouring the server's `Retry-After` response header if present, or falling back to exponential backoff starting at 2 seconds.

## Terminal feedback

Progress is printed to `stderr` throughout the run:

```
Collecting candidates …
  [1/3] Scraping AI ecosystem pages …
    Page 0 …
    …
    Page 10 …
        268 names found
  [2/3] Searching packages.drupal.org for 'ai' …
        649 names found
  [3/3] Searching git.drupalcode.org composer.json for 'drupal/ai' …
        370 project IDs found — resolving names …
        324 unique project names resolved
  812 unique candidates after merge

Verifying via packages.drupal.org p2 files …
  [47/812] issues: skip
  [51/812] ai_provider_openai: ok
  [89/812] deepgram: ok
  …

Fetching module labels + descriptions from info.yml files …
  [1/205] ai_provider_openai: OpenAI Provider +desc
  [2/205] ai_agents: AI Agents +desc
  …

Fetching usage counts …
  [1/205] ai_provider_openai: usage=13133 security_covered=True
  [2/205] ai_agents: usage=10758 security_covered=True
  …

Results: 205 confirmed modules (607 skipped, 3 used date fallback)

Collecting recipe candidates …
  [1/4] Reusing AI ecosystem names not matched as modules …
        86 names
  [2/4] Reusing GitLab search names not matched as modules …
        119 names
  [3/4] Searching Packagist for type=drupal-recipe, q=ai …
        46 names found
  [4/4] Fetching curated AI recipe list …
        10 names found
  198 unique recipe candidates after merge

Verifying recipes via repo.packagist.org p2 files …
  [1/142] ai_recipe_image_classification: ok
  [2/142] drupal_cms_ai: ok
  [3/142] llms_txt: skip
  …

Fetching recipe labels + descriptions from recipe.yml files …
  [1/18] ai_recipe_image_classification: AI Image Classification recipe +desc
  [2/18] drupal_cms_ai: AI Assistant +desc
  …

Fetching recipe stats (downloads + stars) from Packagist …
  [1/18] ai_recipe_image_classification: downloads=1234 stars=56
  [2/18] drupal_cms_ai: downloads=5678 stars=12
  …

Results: 18 confirmed recipes (124 skipped)
```

The p2 verification, info.yml label, recipe p2 verification, and recipe label phases each complete candidates out-of-order (3 workers run concurrently), so progress numbers are not sequential within those phases. The usage-count phase runs in order (recipes never reach that phase — no usage/security call is ever made for them). Every candidate prints a line in each phase — this matters most for the usage-count phase, since it's fully sequential at 3s/module and can otherwise look stalled for many minutes.

Because progress goes to `stderr` and the markdown table goes to `stdout`, they do not interfere when redirecting output to a file.

## Limitations

**Coverage depends on candidate sources.** The script checks candidates drawn from the ecosystem page, a package name search for "ai", and — when a token is available — a GitLab search of every project's `composer.json` content. That third source largely closes the old "no 'ai' in the name" gap, since it matches the dependency itself rather than the name. Two residual gaps remain: without a token that source is skipped (reverting to name-based coverage only), and GitLab blob search indexes only each project's **default branch**, so a `drupal/ai` dependency present solely on a non-default branch is still missed. There is no reverse-dependency index in the Composer protocol that would enable exhaustive enumeration of all 18,000+ packages on packages.drupal.org.

**The latest stable release is preferred.** The script picks the newest stable release that passes all checks. If no stable release exists it falls back to the newest pre-release (rc, beta, alpha, dev). If a module's latest stable release dropped the drupal/ai dependency, the module will not appear even if a dev release still has it.

**Install counts are approximate.** The `project_usage` field from the Drupal.org API reports sites that have submitted an update check recently. It undercounts sites with update checking disabled, and may overcount sites reporting from multiple environments.

**Install counts are absent for newer/less-adopted modules.** Modules with no tracked installs show `—`. This typically means the module is very new, has very low adoption, or is a dev-only tool.

**Results reflect the data sources at the time of the run.** The ecosystem page, package search index, and p2 files all change as new modules are published. Re-run the script periodically for an up-to-date snapshot.

**Python 3.10+ required.** The script uses union type hints (`X | Y`) introduced in Python 3.10.

**Recipe coverage is keyword-narrowed too.** The Packagist recipe search uses `q=ai`, same as the module search — a recipe with no "ai" in its name/description that still hard-depends on `drupal/ai` could be missed. The curated YAML list mitigates known gaps (like `drupal_cms_ai`) but is a supplement, not the primary discovery mechanism; the Packagist type+keyword search is.

**Recipes have no Drupal.org usage or security-coverage data.** This isn't a gap in the script — Drupal.org's API has no `project_usage` field at all for a recipe (confirmed: absent, not zero). Packagist download and star counts are shown instead as substitute popularity signals; note downloads count Composer install events (cumulative total), not active sites like the module usage figures.
