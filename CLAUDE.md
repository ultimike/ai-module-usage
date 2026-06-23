# CLAUDE.md — Project Context for AI Sessions

This file is read automatically by Claude Code at the start of every session.
It documents the design history, API quirks, and improvement ideas for the
`drupal_ai_dependents.py` script so future sessions can continue without
re-discovering everything from scratch.

---

## What the script does

`drupal_ai_dependents.py` finds all Drupal modules that declare a **hard
dependency on [drupal/ai](https://www.drupal.org/project/ai)** in their
`composer.json`, and outputs a sorted markdown table with:

- Label — the module's actual display title, from its `*.info.yml` `name:` key (linked to the project page)
- Machine name — the `name` field from the module's `composer.json` (e.g. `drupal/ai_agents`)
- URL (`https://www.drupal.org/project/{name}`)
- Latest version compatible with Drupal 10 or 11
- Release date
- Security coverage — ✅/🚫 based on Drupal's security advisory policy
- Active install count ("Drupal.org usage")

Run time: **~17–20 minutes**. The p2 verification and info.yml label phases
each use 3 concurrent workers (ThreadPoolExecutor), every worker respecting
its phase's 3s per-worker delay. The drupal.org usage/security API remains
sequential at 3s (most sensitive). Output goes to stdout; progress goes to
stderr.

**Every phase prints one line per module** (`[i/N] {name}: ...`). This
matters most for the usage-count phase, which is fully sequential at
3s/module and can run for many minutes — without per-module output it used
to only print when a release-date fallback was needed (rare), making the
whole run look hung.

```bash
python3 drupal_ai_dependents.py              # print to stdout
python3 drupal_ai_dependents.py -o out.md   # write markdown to file
python3 drupal_ai_dependents.py --json results.json  # save JSON for re-rendering

# Fast re-render from saved JSON (no network calls):
python3 render_md.py results.json -o results.md
python3 render_html.py results.json -o results.html
```

---

## Data sources and why each was chosen

### 1. Candidate discovery — two sources, union-merged

**Source A: `drupal.org/project/ai/ecosystem` (HTML scraping)**
- Scraped with a regex matching `href="/project/([a-z0-9_]+)"`
- Paginates through ~11 pages (~268 unique names)
- Curated by module maintainers — good coverage of intentional AI integrations
- Limitation: only includes modules whose maintainers opted in

**Source B: `packages.drupal.org/8/search.json?s=ai` (JSON API)**
- Paginates using the absolute `next` URL in the response
- Returns ~649 names (server caps `per_page` at 50 regardless of what you request)
- Catches modules with `drupal/ai` dependency that aren't on the ecosystem page
- Limitation: matches on name/description, not on dependency — produces false
  positives that are filtered out in stage 2

Total after dedup: ~733 candidates. Ecosystem names come first; search names
are appended only if not already seen.

### 2. Dependency verification — `packages.drupal.org/files/packages/8/p2/drupal/{name}.json`

This is the **authoritative source**. Each file is the Composer v2 metadata
for one package, containing every published version's full `composer.json`
contents (require, type, extra, etc.).

Three checks are applied (all must pass):
1. `type == "drupal-module"` — excludes recipes (`drupal-recipe`), profiles,
   distributions. Checked on the first (latest) version only, as type is stable.
2. `"drupal/ai"` present in `require` — confirms hard dependency
3. `drupal/core` constraint includes `\b10\b` or `\b11\b` — Drupal 10/11 compat.
   Missing or `"*"` constraint is treated as compatible (the /8 repo only
   serves modern packages).

Also extracts from p2 data:
- `version` — the version string
- `extra.drupal.datestamp` — Unix timestamp for the release date (present on
  most packages; absent on some older ones)

**Important discovery made during development:** The p2 file has NO `time`
field (standard Composer field), but DOES have `extra.drupal.datestamp`.
The `version` string in p2 files matches updates.drupal.org exactly for
modern semver packages (e.g. `1.2.1`), but older `8.x-*` style versions
would differ — those are already filtered out by the D10/11 check anyway.

### 3. Release date fallback — `updates.drupal.org/release-history/{name}/current`

Only called when p2 has no `extra.drupal.datestamp`. Returns XML with all
releases. Tries to match the exact version string; falls back to the first
published release date if not found. In practice very few modules need this.

### 4. Usage/install count — `www.drupal.org/api-d7/node.json`

The only source for install counts. Returns a `project_usage` object like
`{"1.0.x": 1500, "1.1.x": 3200}` — values are **strings, not integers**
(discovered during development; the `int()` cast in the code is intentional).
We sum all version counts for a total.

The API node `type` field here says `project_general` for recipes/profiles,
`project_module` for real modules — but we no longer use this for type-checking
(the p2 `type` field is more reliable and saves an API round-trip for
candidates that fail verification).

This same API call also returns `field_security_advisory_coverage`
(`"covered"` or `"not-covered"`) — `get_usage_and_security()` reads both
fields from one response instead of making two separate calls.

### 5. Module label — `git.drupalcode.org/project/{name}/-/raw/{version}/{name}.info.yml`

The project's actual display title lives in the `name:` key of its main
`.info.yml` file — it is **not** part of composer.json or any JSON API
response. Drupal.org's GitLab instance serves raw repo files at this URL
pattern, tag-matched to the same version string returned by the p2 file
(confirmed working for both old-style `8.x-*` tags and modern semver tags).
Parsed with a simple regex (`_INFO_NAME_RE`) rather than a YAML library,
since `pyyaml` isn't a stdlib dependency and the script only needs one
top-level scalar key. Falls back to `human_name(machine_name)` (title-cased
machine name) if the fetch or parse fails.

---

## API quirks and gotchas discovered during development

| API | Quirk |
|-----|-------|
| `www.drupal.org/api-d7/node.json` | Returns HTTP 503 when called too fast. Respects `Retry-After` header. Values in `project_usage` are strings, not ints. `field_security_advisory_coverage` is `"covered"`/`"not-covered"` (string, not bool). |
| `packages.drupal.org/8/search.json` | `per_page=100` is silently capped at 50. The `next` URL is absolute (starts with `https://`). |
| `packages.drupal.org` p2 files | No `time` field. Date is in `extra.drupal.datestamp` as a Unix timestamp string. `type` for recipes is `drupal-recipe`, not `project_general`. Each version entry already includes the original composer.json `name` field (e.g. `drupal/ai_agents`) — no extra request needed for the "machine name" column. |
| `updates.drupal.org` | Returns XML (not JSON). More tolerant of fast requests than the Drupal.org JSON API. |
| `drupal.org/project/ai/ecosystem` | Page 0 has no `?page=0` parameter (omit it). Pagination detected by checking if `?page=N` appears in the HTML. |
| `git.drupalcode.org` raw files | Tag names match the p2 `version` string directly (no `8.x-` prefix needed for modern packages). 404s if the module's main `.info.yml` isn't at the repo root under `{machine_name}.info.yml` (rare; handled by falling back to the title-cased machine name). |

---

## Design decisions and what was tried first

**Packagist was tried first** (`packagist.org/packages/drupal/ai/dependents.json`).
It only found 28 non-abandoned `drupal/*` packages, and all of them were
recipes/distributions (`project_general` type) with no install tracking.
Abandoned because it only captures packages that explicitly list `drupal/ai`
in their `composer.json` AND are mirrored to Packagist — most Drupal modules
are only on `packages.drupal.org`, not the main Packagist registry.

**Ecosystem page alone was tried next.** This found ~186 modules but included
false positives — modules like `llms_txt`, `markdownify`, and `maestro` that
appear on the ecosystem page as companions/integrations but don't actually
have `drupal/ai` in their `require`. These were correctly excluded once p2
verification was added.

**Full enumeration of all 18,825 packages on packages.drupal.org was
considered and rejected.** At 3s per package that would take ~15 hours.
The two-source candidate strategy (ecosystem page + name search) is a
practical compromise that covers the vast majority of real dependents.

**The Drupal.org API node type check was replaced by the p2 type check.**
Originally the script used `www.drupal.org/api-d7` to confirm `project_module`
type before fetching release info. Once p2 verification was added, the API
type check became redundant — p2's `type` field is more reliable and we get
it for free in the same request.

**Delays are placed BEFORE requests, not after.** An earlier version put
`time.sleep()` after each call, meaning the first request in any sequence
had zero lead-in gap. The current version sleeps before every call.

---

## Rate limiting configuration

All delay constants are at the top of the script (~line 43):

```python
ECOSYSTEM_PAGE_DELAY = 3.0   # www.drupal.org ecosystem pages
PACKAGES_DELAY       = 3.0   # packages.drupal.org (search + p2)
RELEASE_DELAY        = 3.0   # updates.drupal.org (fallback only)
DRUPAL_API_DELAY     = 3.0   # www.drupal.org JSON API
GITLAB_DELAY         = 3.0   # git.drupalcode.org (info.yml label fetch)
```

The Drupal.org JSON API is the most sensitive — it returned 503s during
development when called faster than ~1 per second. 3 seconds is conservative.
`updates.drupal.org` is more tolerant (it handles all Drupal site cron checks
globally) but we use 3s there too for consistency.

The 503 retry logic in `_drupal_api_get()` reads the `Retry-After` response
header if present, falling back to exponential backoff starting at 2 seconds.

**Concurrency note:** The p2 verification phase and the info.yml label phase
each run 3 concurrent workers via `ThreadPoolExecutor(max_workers=3)`. Every
worker calls `time.sleep(PACKAGES_DELAY)` or `time.sleep(GITLAB_DELAY)` before
its own request — there is no shared rate limiter, so each upstream sees up
to 3 simultaneous requests at burst starts, then ~1 req/s at steady state.
This is safe for both CDN-backed static file servers (packages.drupal.org and
git.drupalcode.org). The drupal.org JSON API (usage counts + security
coverage) remains strictly sequential — one request every 3s.

---

## Known limitations

1. **Coverage gap:** Modules that depend on `drupal/ai` but have no "ai"
   connection in their name/description AND aren't on the ecosystem page
   will be missed. There is no reverse-dependency index in the Composer
   protocol to close this gap without enumerating all 18,825 packages.

2. **Stable version preferred:** The script collects all versions that pass the
   three checks, then picks the newest stable release. If no stable release
   exists it falls back to the newest pre-release (rc → beta → alpha → dev).
   If the very latest stable release dropped the `drupal/ai` dependency, the
   module disappears from results even if older releases had it.

3. **No `time` field in p2:** The `extra.drupal.datestamp` fallback works for
   most packages, but some older or unusual packages have neither — those get
   a date fallback from `updates.drupal.org`, or `—` if that also fails.

4. **Install counts undercount:** Sites with Drupal's update checking disabled
   don't report usage. The count is a lower bound, not an exact figure.

---

## Possible future improvements

- **`--drupal-versions` flag** to let the user filter by specific Drupal major
  versions (e.g. `--drupal-versions 11` for D11-only).

- ~~**`--stable-only` flag**~~ — stability filter added to `render_html.py` via checkboxes.

- **Output format options** — ~~HTML output added (`--html FILE`)~~. ~~JSON output added (`--json FILE`)~~. CSV still possible for spreadsheet/tool import.

- **Caching between runs** — save the p2 responses locally (e.g. to a `.cache/`
  directory) so re-runs don't re-fetch every package. p2 files rarely change
  and the main cost is the 3s delay per request.

- **Delta reporting** — compare two runs and show what's new, what changed
  version, and what disappeared. Useful for tracking ecosystem growth over time.

- **`--candidate-source` flag** to let the user restrict to one source
  (ecosystem-only or search-only) for faster targeted runs.

- **Additional search terms** — the search currently only queries `"ai"`.
  Running additional queries (e.g. `"llm"`, `"openai"`, `"anthropic"`) and
  merging results might surface more dependents with non-"ai" names.

---

## HTML output implementation

There are two HTML renderers:

**`render_html(rows)` in `drupal_ai_dependents.py`** — original, called by `--html FILE`.
Accepts a list of row dicts directly. No stability badges or filter checkboxes.
Kept for backward compatibility with the `--html` shortcut.

**`render_html.py`** — the recommended renderer, reads `results.json`. Key additions over
the original:
- Each `<tr>` has `data-stability="stable|rc|beta|alpha|dev"` and
  `data-security="covered|not-covered"`.
- Version cell shows a small colored badge (green=stable, blue=rc, yellow=beta, orange=alpha, grey=dev).
- Controls bar has stability checkboxes (`.stab-cb`) and security coverage
  checkboxes (`.sec-cb`), all checked by default; unchecking a value hides
  matching rows.
- Combined filter: row visible when `nameMatches(row) AND stabChecked.has(row.dataset.stability) AND secChecked.has(row.dataset.security)`.

Shared design notes for both renderers:
- CSS and JS are stored as regular Python string variables (not f-strings) to
  avoid the need to escape every `{` and `}`. Only the final HTML assembly
  uses f-strings for the handful of variable substitutions.
- All cell values used for sorting are stored in `data-val` attributes on each
  `<td>`. Sorting never reads rendered text — it reads the raw value:
  - Label (col 0): plain text (for both sorting and the name filter)
  - Machine name (col 1): plain text, not linked
  - Version (col 2): raw semver string; `""` for missing (sorts to bottom)
  - Released (col 3): `YYYY-MM-DD` string; `""` for `"—"` (sorts to bottom)
  - Security coverage (col 4): `"1"`/`"0"`, sorted numerically
  - Drupal.org usage (col 5): integer as string; `""` for unknown (treated as -Infinity)
- Sort state is tracked with `sortCol` (column index, -1 = none) and
  `sortDir` (1 = asc, -1 = desc). First click on a text column → ascending;
  first click on a numeric column → descending. Re-click reverses. Default
  sort on load is column 5 (usage), descending.
- `html.escape()` is used on all row values to prevent XSS from any
  unexpected characters in API responses.
- Security coverage uses `SECURITY_COVERED_EMOJI` (✅) / `SECURITY_NOT_COVERED_EMOJI`
  (🚫) — defined once in `drupal_ai_dependents.py` and duplicated as local
  constants in `render_md.py` / `render_html.py` since those are standalone
  scripts that only read `results.json`, not importable modules.

---

## JSON output and rendering pipeline

`--json FILE` writes a structured payload after the sort step:

```json
{
  "generated":         "2026-06-22",
  "drupal_versions":   [10, 11],
  "modules": [
    {
      "machine_name":     "drupal/ai_provider_openai",
      "label":            "OpenAI Provider",
      "url":              "https://www.drupal.org/project/ai_provider_openai",
      "version":          "1.2.1",
      "release_date":     "2026-02-25",
      "usage":            13133,
      "security_covered": true,
      "stability":        "stable"
    }
  ]
}
```

- `machine_name` is the composer.json `name` field for the package (e.g.
  `drupal/ai_provider_openai`), taken from the same p2 response already
  fetched for verification — sourced from `best.get("name")` in `get_p2_info()`,
  stored as `composer_name` internally and copied into the row's `machine_name`
  key. There is no separate "bare machine name" field in the JSON output; the
  bare name (e.g. `ai_provider_openai`) only exists as a local variable used
  to build URLs and fetch requests.
- `label` is the `name:` key from the module's `*.info.yml`, fetched by
  `get_module_label()`. Falls back to `human_name(machine_name)` (title-cased)
  when the file can't be fetched or parsed.
- `security_covered` is a bool from `get_usage_and_security()`, derived from
  `field_security_advisory_coverage == "covered"` on the same Drupal.org API
  node already fetched for usage counts.
- `stability` is computed by `detect_stability(version)` in the main script using
  `_STABILITY_RE = re.compile(r'-(?P<level>alpha|beta|rc|dev)', re.IGNORECASE)`.
  Empty/missing version strings default to `"stable"`.

Old `results.json` files without the `stability` field are handled gracefully
in `render_html.py` via `r.get("stability", "stable")`. Older files predating
the `label`/`machine_name`(composer-name)/`security_covered` fields are
**not** compatible with the current renderers — re-run the main script to
regenerate `results.json` before re-rendering.

Recommended workflow:
```bash
python3 drupal_ai_dependents.py --json results.json   # slow, once
python3 render_md.py results.json -o results.md       # fast, re-run anytime
python3 render_html.py results.json -o results.html   # fast, re-run anytime
```

---

## File inventory

| File | Purpose |
|------|---------|
| `drupal_ai_dependents.py` | Main script — data collection and candidate verification |
| `render_md.py` | Markdown renderer — reads `results.json`, outputs markdown table |
| `render_html.py` | HTML renderer — reads `results.json`, outputs HTML with stability filter |
| `README.md` | User-facing documentation (usage, limitations, how it works) |
| `CLAUDE.md` | This file — context for future Claude Code sessions |
