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

- Module name (human-readable)
- URL (`https://www.drupal.org/project/{name}`)
- Latest version compatible with Drupal 10 or 11
- Release date
- Active install count

Run time: **~45–60 minutes** due to 3-second polite delays before every
HTTP request. Output goes to stdout; progress goes to stderr.

```bash
python3 drupal_ai_dependents.py              # print to stdout
python3 drupal_ai_dependents.py -o out.md   # write to file
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

---

## API quirks and gotchas discovered during development

| API | Quirk |
|-----|-------|
| `www.drupal.org/api-d7/node.json` | Returns HTTP 503 when called too fast. Respects `Retry-After` header. Values in `project_usage` are strings, not ints. |
| `packages.drupal.org/8/search.json` | `per_page=100` is silently capped at 50. The `next` URL is absolute (starts with `https://`). |
| `packages.drupal.org` p2 files | No `time` field. Date is in `extra.drupal.datestamp` as a Unix timestamp string. `type` for recipes is `drupal-recipe`, not `project_general`. |
| `updates.drupal.org` | Returns XML (not JSON). More tolerant of fast requests than the Drupal.org JSON API. |
| `drupal.org/project/ai/ecosystem` | Page 0 has no `?page=0` parameter (omit it). Pagination detected by checking if `?page=N` appears in the HTML. |

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
```

The Drupal.org JSON API is the most sensitive — it returned 503s during
development when called faster than ~1 per second. 3 seconds is conservative.
`updates.drupal.org` is more tolerant (it handles all Drupal site cron checks
globally) but we use 3s there too for consistency.

The 503 retry logic in `_drupal_api_get()` reads the `Retry-After` response
header if present, falling back to exponential backoff starting at 2 seconds.

---

## Known limitations

1. **Coverage gap:** Modules that depend on `drupal/ai` but have no "ai"
   connection in their name/description AND aren't on the ecosystem page
   will be missed. There is no reverse-dependency index in the Composer
   protocol to close this gap without enumerating all 18,825 packages.

2. **Latest version only:** The script checks versions newest-first and stops
   at the first one that passes all checks. If the very latest release dropped
   the `drupal/ai` dependency, the module disappears from results even if
   older releases had it.

3. **No `time` field in p2:** The `extra.drupal.datestamp` fallback works for
   most packages, but some older or unusual packages have neither — those get
   a date fallback from `updates.drupal.org`, or `—` if that also fails.

4. **Install counts undercount:** Sites with Drupal's update checking disabled
   don't report usage. The count is a lower bound, not an exact figure.

---

## Possible future improvements

- **`--drupal-versions` flag** to let the user filter by specific Drupal major
  versions (e.g. `--drupal-versions 11` for D11-only).

- **`--stable-only` flag** to exclude alpha/beta/RC releases from the results.

- **Output format options** — ~~HTML output added (`--html FILE`)~~. CSV or
  JSON still possible for spreadsheet/tool import.

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

## HTML output implementation (added 2026-06-22)

`render_html(rows)` in `drupal_ai_dependents.py` builds a single self-contained
`.html` file with no external dependencies. Key design notes:

- CSS and JS are stored as regular Python string variables (not f-strings) to
  avoid the need to escape every `{` and `}`. Only the final HTML assembly
  uses f-strings for the handful of variable substitutions.
- All cell values used for sorting are stored in `data-val` attributes on each
  `<td>`. Sorting never reads rendered text — it reads the raw value:
  - Module name: plain text (for both sorting and filtering)
  - Version: raw semver string; `""` for missing (sorts to bottom)
  - Released: `YYYY-MM-DD` string; `""` for `"—"` (sorts to bottom)
  - Installs: integer as string; `""` for unknown (treated as -Infinity)
- Sort state is tracked with `sortCol` (column index, -1 = none) and
  `sortDir` (1 = asc, -1 = desc). First click on a text column → ascending;
  first click on a numeric column → descending. Re-click reverses.
- Filter operates on `data-val` of column 0 (module name), case-insensitive.
- `html.escape()` is used on all row values to prevent XSS from any
  unexpected characters in API responses.
- Triggered via `--html FILE` CLI flag; can be combined with `-o` for both
  markdown and HTML in a single run.

---

## File inventory

| File | Purpose |
|------|---------|
| `drupal_ai_dependents.py` | The script |
| `README.md` | User-facing documentation (usage, limitations, how it works) |
| `CLAUDE.md` | This file — context for future Claude Code sessions |
