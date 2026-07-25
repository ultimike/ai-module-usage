# CLAUDE.md — Project Context for AI Sessions

This file is read automatically by Claude Code at the start of every session.
It documents the design history, API quirks, and improvement ideas for the
`drupal_ai_dependents.py` script so future sessions can continue without
re-discovering everything from scratch.

---

## What the script does

`drupal_ai_dependents.py` finds all Drupal **modules and recipes** that
declare a **hard dependency on [drupal/ai](https://www.drupal.org/project/ai)**
in their `composer.json`. It only collects and verifies data, writing the
result as JSON — rendering is `render_md.py`'s and `render_html.py`'s job
(two sorted tables: Modules, then Recipes — separate `##` sections in
markdown, separate tabs in HTML).

**Modules table:**
- Label — the module's actual display title, from its `*.info.yml` `name:` key (linked to the project page)
- Description — the module's description, from its `*.info.yml` `description:` key
- Machine name — the `name` field from the module's `composer.json` (e.g. `drupal/ai_agents`)
- URL (`https://www.drupal.org/project/{name}`)
- Latest version compatible with Drupal 10 or 11
- Release date
- Security coverage — three states based on Drupal's security advisory policy and stability:
  - Filled shield (`images/shield-icon-black.svg`, 50% opacity) — covered + stable release
  - Outline shield (inline SVG, stroke only, 50% opacity) — covered + pre-release (rc/beta/alpha/dev)
  - 🚫 — not covered by the security advisory policy
- Active install count ("Drupal.org usage")
- Categories — one or more categories derived from the label + description +
  machine name (see the Categorization section below); shown as pills in HTML,
  comma-separated in markdown

**Recipes table:** Label, Description (same source/treatment as modules, but
from `recipe.yml`'s `description:` key), machine name, URL, version, release
date, Packagist downloads, Packagist stars, Categories — no Drupal.org security
coverage or usage columns.
Recipes (Drupal projects of type `drupal-recipe`, applied via `drush recipe`
rather than installed as code) genuinely have neither of those: `project_usage`
is absent from Drupal.org's API response for a recipe (not zero — absent),
confirmed live, and recipes aren't on packages.drupal.org at all to begin with
(see Data Sources §6-9 below). The table omits both Drupal.org columns rather
than inventing an "N/A" state. Packagist total-download counts (see §10 below)
are a meaningful substitute popularity signal and are shown in the downloads
column instead.

Run time: **varies, roughly 15-30+ minutes** depending on candidate count and
upstream response times. Every phase — candidate discovery, p2 verification,
info.yml/recipe.yml label fetches, usage/security lookups, recipe stats —
makes requests **sequentially from a single thread, one at a time**, with no
fixed client-side delay between them. This is a deliberate choice: direct
feedback from the drupal.org sysadmins was that concurrency, not request
rate, is what risks looking like a DoS server-side, and that self-throttling
delays aren't necessary as long as HTTP 429 responses are respected. See
"Rate limiting configuration" below. Descriptions add **no** network
requests: they're read from the info.yml/recipe.yml already fetched for the
label, in the same parse. JSON goes to stdout (or a file with `--json`);
progress goes to stderr.

**Every phase prints one line per module** (`[i/N] {name}: ...`). This
matters most for the usage-count phase, which is fully sequential and can
run for many minutes — without per-module output it used to only print when
a release-date fallback was needed (rare), making the whole run look hung.

```bash
python3 drupal_ai_dependents.py --json results.json  # slow, once

# Fast re-render from saved JSON (no network calls):
python3 render_md.py results.json -o results.md
python3 render_html.py results.json -o results.html
```

There used to be `-o`/`--output` and `--html` flags that rendered markdown
and a (simpler, modules-only) HTML table directly inside this script. They
were removed: that inline rendering didn't get recipe support, stability
badges, or filter checkboxes when `render_html.py` gained them, so it
silently fell out of sync. Now there's exactly one renderer per output
format, and this script's only job is producing correct, complete JSON.

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

### 5. Module label + description — `git.drupalcode.org/project/{name}/-/raw/{version}/{name}.info.yml`

The project's actual display title lives in the `name:` key of its main
`.info.yml` file — it is **not** part of composer.json or any JSON API
response. Drupal.org's GitLab instance serves raw repo files at this URL
pattern, tag-matched to the same version string returned by the p2 file
(confirmed working for both old-style `8.x-*` tags and modern semver tags).
`get_module_label_and_description()` reads **both** the `name:` (label) and
`description:` keys from this single fetch — the description is the module's
own full text, so no separate request or other source is ever consulted for
it. Parsed with simple regexes (`_INFO_NAME_RE`, `_INFO_DESCRIPTION_RE`) via
the shared `_parse_info_yml()` helper rather than a YAML library, since
`pyyaml` isn't a stdlib dependency and the script only needs two top-level
scalar keys (block scalars `>`/`|` are treated as absent — see
`_YAML_BLOCK_INDICATOR_RE`). The label falls back to `human_name(machine_name)`
(title-cased machine name) if the fetch or parse fails; the description is
simply absent (`None`) when the info.yml has no `description:` key.

### 6. Recipe candidate discovery — Packagist type+keyword search

**Recipes are not on packages.drupal.org at all** — confirmed live: P2_URL
returns HTTP 404 for any recipe. They ARE on the regular Packagist registry
(`packagist.org`/`repo.packagist.org`), which is a completely separate
service from packages.drupal.org despite the similar name.
`packagist.org/search.json?type=drupal-recipe` alone returns ~670 packages
regardless of AI-relatedness — verifying all of them would roughly double
this script's run time. Adding `q=ai` narrows that to ~46-60 while still
catching every recipe tested during development (including
`drupal/drupal_cms_ai`). This accepts the same "could miss a non-'ai'-named
dependent" tradeoff already made by the module search in §1 Source B.

The candidate pool also reuses ecosystem-page names that failed module
verification (free — no new request; a name can't be both a confirmed
module and a recipe) rather than re-querying packages.drupal.org.

### 7. Recipe candidate discovery — curated AI recipe list (supplement)

`git.drupalcode.org/project/ai_dashboard/-/raw/1.0.x/ai_dashboard_recommended_recipes.yml`
is a hand-maintained list from the AI Dashboard module, not authoritative.
Confirmed useful: it catches `drupal/drupal_cms_ai`, a real recipe with a
hard `drupal/ai` dependency that does **not** appear on the ecosystem page
at all. Every name from this file still goes through full p2 verification
against Packagist — the YAML contents are never trusted directly. Parsed
with a loose regex (`_CURATED_MACHINE_NAME_RE`) extracting `machineName:`
values, same no-YAML-library approach as `_INFO_NAME_RE`.

### 8. Recipe verification — `repo.packagist.org/p2/drupal/{name}.json`

Same Composer v2 p2 format as packages.drupal.org, just a different host
and a different required `type` (`drupal-recipe` instead of
`drupal-module`). `_select_best_ai_dependent_version()` is shared between
`get_p2_info()` (modules) and `get_recipe_info()` (recipes) — the
type/require/core-constraint filtering logic is identical, only the URL and
date-extraction differ.

**Unlike packages.drupal.org's p2 files, Packagist's have a real ISO-8601
`time` field on every version** — no `extra.drupal.datestamp` workaround,
and no `updates.drupal.org` date fallback is needed at all for recipes.

**Important discovery made during development:** Composer splits
stable/tagged releases from branch/dev snapshots into *separate* p2 files —
`p2/drupal/{name}.json` (stable/tagged) vs `p2/drupal/{name}~dev.json`
(dev). A recipe with only a `"1.x-dev"` release (no tagged release yet,
e.g. `ai_recipe_audio_transcription`) returns an **empty** version list
from the main URL — its data only exists in the `~dev` file. Confirmed
live. `get_recipe_info()` fetches and merges both files before running
verification, so dev-only recipes still resolve correctly. This same split
likely also affects the module pipeline (a module with only a dev release
would currently be invisible to `get_p2_info()`), but that's an existing,
unaddressed gap — out of scope for this change (see Known Limitations).

### 10. Recipe stats (downloads + stars) — `packagist.org/packages/drupal/{name}.json`

Each verified recipe's download count and star count are fetched from this
endpoint in **a single request** — both values live in the same JSON response,
so there is no extra network overhead for stars vs. downloads.

- `package.downloads.total` — integer count of all Composer installs recorded
  for the package. Closest available substitute for Drupal.org `project_usage`.
- `package.favers` — integer count of Packagist "star" events (the ★ button on
  a package's Packagist page). Packagist's internal name for this field is
  `favers`; the UI and our output label both call it "stars".

Fetched in a dedicated sequential phase (`get_recipe_stats()`, single thread,
no client-side delay) after recipe label fetching and before assembling
`recipe_rows`. Both values are `None` on any error (non-200, missing key, JSON
decode failure). A value of `0` is a real zero (package exists but has no
downloads/stars yet). Both keys are **absent from module rows** — module
packages live only on packages.drupal.org, not the main Packagist registry.

### 9. Recipe label + description — `git.drupalcode.org/project/{name}/-/raw/{ref}/recipe.yml`

Recipes don't have a `{name}.info.yml` — their display title and description
live in the `name:` / `description:` keys of a **fixed-filename** `recipe.yml`
at the repo root instead (not name-templated, unlike modules' info.yml).
`get_recipe_label_and_description()` reads both from this single fetch via the
same shared `_parse_info_yml()` helper as modules, since those top-level keys
aren't module-specific. The description is absent (`None`) when the recipe.yml
has no `description:` key — no other source is consulted.

**Important discovery made during development:** Composer's `"-dev"`
version suffix (e.g. `"1.x-dev"`) is not a real git ref — GitLab has no
`"1.x-dev"` branch or tag, only `"1.x"`. Confirmed live:
`.../-/raw/1.x/composer.json` → 200, `.../-/raw/1.x-dev/composer.json` →
404. `_git_ref_for_version()` strips the suffix before building the
recipe.yml URL. This same gap likely exists latently in the module label
path too (untested — no AI module in this dataset currently has only a dev
release), but retrofitting it is out of scope for this change.

---

## API quirks and gotchas discovered during development

| API | Quirk |
|-----|-------|
| `www.drupal.org/api-d7/node.json` | Returns HTTP 503 when called too fast. Respects `Retry-After` header. Values in `project_usage` are strings, not ints. `field_security_advisory_coverage` is `"covered"`/`"not-covered"` (string, not bool). |
| `packages.drupal.org/8/search.json` | `per_page=100` is silently capped at 50. The `next` URL is absolute (starts with `https://`). |
| `packages.drupal.org` p2 files | No `time` field. Date is in `extra.drupal.datestamp` as a Unix timestamp string. `type` for recipes is `drupal-recipe`, not `project_general`. Each version entry already includes the original composer.json `name` field (e.g. `drupal/ai_agents`) — no extra request needed for the "machine name" column. |
| `updates.drupal.org` | Returns XML (not JSON). More tolerant of fast requests than the Drupal.org JSON API. |
| `drupal.org/project/ai/ecosystem` | Page 0 has no `?page=0` parameter (omit it). Pagination detected by checking if `?page=N` appears in the HTML. |
| `git.drupalcode.org` raw files | Tag names match the p2 `version` string directly (no `8.x-` prefix needed for modern packages). 404s if the module's main `.info.yml` isn't at the repo root under `{machine_name}.info.yml` (rare; handled by falling back to the title-cased machine name). Both the `name:` (label) and `description:` keys are read from this one file — info.yml is the **only** source for the module description. |
| `packagist.org/search.json` | Unlike `packages.drupal.org/8/search.json`, `per_page=100` is actually honored (not capped at 50). Returns all packages of the given `type` regardless of AI-relatedness — same false-positive-tolerant design as the module search. |
| `repo.packagist.org` p2 files | Same Composer v2 p2 shape as packages.drupal.org's, but has a real `time` field — no datestamp workaround needed. **Splits stable/tagged releases from dev/branch snapshots into separate files** (`{name}.json` vs `{name}~dev.json`) — a dev-only package returns an empty version list from the main URL; both must be fetched and merged. |
| `git.drupalcode.org` recipe.yml | Fixed filename (`recipe.yml`, not `{name}.info.yml`). Composer `"x.y-dev"` version strings are NOT real git refs — GitLab serves the underlying branch (e.g. `"1.x"`) with the `-dev` suffix stripped. Both the `name:` (label) and `description:` keys are read from this one file — recipe.yml is the **only** source for the recipe description. |
| curated YAML (`ai_dashboard_recommended_recipes.yml`) | Hand-maintained, not authoritative — every name still goes through full p2 verification. Catches at least one recipe (`drupal_cms_ai`) absent from the ecosystem page. |
| `packagist.org` stats API | `packagist.org/packages/drupal/{name}.json` → `package.downloads.total` (downloads) and `package.favers` (stars). Both values come from the same response; no second request is made. Only called for recipes (not modules). Returns HTTP 404 for packages not on main Packagist — but only verified recipes are queried here, so 404s shouldn't occur in practice. |

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

**Delays are placed BEFORE requests, not after.** (Historical — superseded
2026-07-24, see "Rate limiting configuration": there are no client-side
delays left at all.) An earlier version put `time.sleep()` after each call,
meaning the first request in any sequence had zero lead-in gap; a later
version moved every sleep to before its call. Left here for context in case
delays are ever reintroduced.

**Self-throttling delays and concurrent worker pools were removed
entirely (2026-07-24).** The script previously used per-host `*_DELAY`
sleep constants plus `ThreadPoolExecutor(max_workers=3)` in the p2/label/
stats phases. Direct feedback from the drupal.org sysadmins was that
concurrency — not request rate — is what risks looking like a DoS
server-side, and that client-side throttling is unnecessary as long as
HTTP 429 (with its `Retry-After` header) is respected. All request-issuing
code now runs sequentially in the main thread with no artificial delay; see
"Rate limiting configuration" below for the current `_http_get()` 429
handling.

**A second registry (Packagist proper) had to be introduced for recipes.**
The single-registry design that works for modules doesn't extend to
recipes — packages.drupal.org simply doesn't carry them (confirmed via live
404). This is why recipe verification hits `repo.packagist.org` instead of
`packages.drupal.org`, and recipe discovery hits `packagist.org` instead of
`packages.drupal.org/8/search.json` — they're unrelated services with
similar names.

**The project description comes only from info.yml/recipe.yml — the
`<meta name="description">` source was considered and dropped.** The project
page's meta tag (`www.drupal.org/project/{name}`) was prototyped as a source
(and as a fallback for projects whose info.yml/recipe.yml lacks a
`description:` key), but it was rejected: it's a separate www.drupal.org
request per project on the most rate-sensitive host (and would have made
recipes touch www.drupal.org, which they otherwise never do), and it returns
a **truncated** body summary rather than the maintainer-authored description.
The info.yml/recipe.yml `description:` key is the full text and is already
fetched for the label, so it's both better and free. A project with no
`description:` key simply has no description (`null`) — do not re-add the meta
fallback without a strong reason.

---

## Rate limiting configuration

**As of 2026-07-24, the script has no client-side throttling delays and
makes no concurrent requests.** This replaced an earlier design (fixed
per-host `*_DELAY` sleep constants plus `ThreadPoolExecutor(max_workers=3)`
pools in the p2/label/stats phases) after direct feedback from the
drupal.org sysadmins:

> Make requests from a single thread. Throttling is not necessary, its
> concurrency we're concerned with. If something starts saturating
> processes server-side with concurrent requests, that's when it gets to
> DoS territory. There are general rate limits in place, so respect the
> retry-after header if you get an HTTP 429 response. Self-throttling is
> not necessary as I said. Handling 429 responses is.

So the current design is:

- **Every phase runs sequentially in the main thread** — candidate
  discovery, p2/recipe verification, info.yml/recipe.yml label fetches,
  usage/security lookups, and recipe stats all issue one request, wait for
  the response, then issue the next. No `ThreadPoolExecutor`, no `threading`
  module, no worker pools anywhere in the script.
- **No fixed pre-request sleep.** The old `*_DELAY` constants
  (`ECOSYSTEM_PAGE_DELAY`, `PACKAGES_DELAY`, `RELEASE_DELAY`,
  `DRUPAL_API_DELAY`, `GITLAB_DELAY`, `PACKAGIST_DELAY`) were removed
  entirely rather than set to `0` — the sysadmin feedback was explicit that
  guessing a delay in advance is unnecessary work, not just unnecessary at
  a particular value.
- **HTTP 429 is retried and honors `Retry-After`.** `_http_get()` — the
  single shared HTTP function every request in the script goes through —
  catches 429 responses specifically, reads the `Retry-After` header (or
  falls back to `RETRY_BACKOFF * 2**attempt` if the header is absent), sleeps
  that long, and retries, up to `MAX_RETRIES` attempts. Any other status
  (including a 429 with no retries left) is returned to the caller as-is.
  This generalizes the retry behavior to every host the script talks to, not
  just the Drupal.org JSON API.
- **`_drupal_api_get()` still separately retries on HTTP 503** (a status
  specific to the Drupal.org JSON API, seen during development when it was
  called faster than ~1/sec) — that logic is unchanged and layers on top of
  `_http_get()`'s 429 handling underneath it.

Run time is no longer a fixed ~20-25 minutes — without artificial delays
between requests it's dominated by upstream response latency and however
often 429/503 retries actually trigger, which will vary run to run.

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

5. **Recipe candidate coverage is keyword-narrowed, like module search:**
   `q=ai` on the Packagist recipe search could miss a recipe with no "ai" in
   its name/description that still hard-depends on `drupal/ai`. The
   ecosystem-page-reuse and curated-YAML sources mitigate this somewhat but
   don't close it entirely — same shape of gap as limitation #1, just for a
   different registry.

6. **Curated recipe YAML is a supplement, not the primary source:** the
   Packagist type+keyword search (§6 above) is what makes recipe discovery
   comprehensive; the curated YAML just catches specific known gaps (like
   `drupal_cms_ai`) in case the search misses something.

7. **Dev-version git-ref gap likely exists in the module label path too:**
   `_git_ref_for_version()` (stripping `-dev` suffixes for GitLab raw-file
   URLs) was added for recipes, where a dev-only release was confirmed live.
   The module label path (`get_module_label()`) has the same theoretical
   gap — a module with only a dev release would currently fail its
   info.yml fetch — but this is untested/unaddressed, since no AI module in
   the current dataset has only a dev release.

8. **The packages.drupal.org p2 stable/dev file split may affect modules
   too:** `get_recipe_info()` fetches both `{name}.json` and `{name}~dev.json`
   p2 files because Packagist splits stable and dev releases between them —
   confirmed live. `get_p2_info()` (modules) only fetches the main file. If
   packages.drupal.org follows the same Composer p2 convention, a module
   with only a dev release would currently be invisible to this script.
   Unverified and unaddressed — out of scope for this change.

---

## Possible future improvements

- **`--drupal-versions` flag** to let the user filter by specific Drupal major
  versions (e.g. `--drupal-versions 11` for D11-only).

- ~~**`--stable-only` flag**~~ — stability filter added to `render_html.py` via checkboxes.

- **Output format options** — ~~HTML output added (`--html FILE`)~~ (later
  removed — `drupal_ai_dependents.py` now only writes JSON;
  `render_html.py` is the sole HTML renderer). ~~JSON output added
  (`--json FILE`)~~. CSV still possible via a new `render_csv.py` for
  spreadsheet/tool import.

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

`drupal_ai_dependents.py` does **not** render HTML (or markdown) itself —
it only collects, verifies, and writes JSON. `render_html.py` is the sole
HTML renderer, reading `results.json`.

(Earlier versions had a second, simpler HTML renderer — `render_html(rows)`
inside `drupal_ai_dependents.py`, used via a `--html FILE` shortcut. It was
removed once recipe support was added: it never gained stability badges,
filter checkboxes, or a Recipes tab, so it silently fell further out of
sync with `render_html.py` every time the real renderer changed — exactly
the kind of drift `--json`-only output is meant to prevent. There is no
replacement; use `render_html.py` directly.)

**`render_html.py`** features:
- Each module `<tr>` has `data-stability="stable|rc|beta|alpha|dev"` and
  `data-security="covered|not-covered"`. **Both** module and recipe `<tr>`s also
  carry `data-categories="Cat A|Cat B"` (pipe-delimited) for the category filter
  — this is the recipe rows' only data attribute.
- Each row's Label cell renders the project's `description` (when present) as a
  muted second line (`<div class="desc">`) beneath the linked label, on **both**
  the Modules and Recipes tabs. It's **display-only**: the Label column's
  `data-val` stays the bare label text, so column sorting and the name filter
  are unchanged (descriptions are not searched or sorted). `render_md.py` does
  the equivalent with `<br><sub>…</sub>` via a shared `_label_cell()` helper that
  HTML-escapes `<`/`>`/`&` and backslash-escapes any `|` (which would otherwise
  break the markdown table row).
- Version cell shows a small colored badge (green=stable, blue=rc, yellow=beta, orange=alpha, grey=dev).
- Categories cell (last column) renders each category as a `.cat-pill` span;
  `data-val` is the categories joined into one string so the column still sorts.
- Controls bar (modules) has stability checkboxes (`.stab-cb`), security
  checkboxes (`.sec-cb`), and category checkboxes (`.cat-cb`), all checked by
  default; unchecking a value hides matching rows. The recipes controls have a
  name filter plus their own `.cat-cb` category checkboxes (no stability/security).
  Category checkboxes are scoped per tab (`#panel-modules .cat-cb` /
  `#panel-recipes .cat-cb`), so the tabs filter independently; only categories
  actually present in a tab get a checkbox.
- Combined modules filter: row visible when `nameMatches(row) AND
  stabChecked.has(stability) AND secChecked.has(security) AND catMatch`, where
  `catMatch` is **match-any** — true when at least one of the row's categories
  is checked (or the row has none). Recipes filter = `nameMatches AND catMatch`.
- **Tabbed Modules/Recipes interface:** two `.tab-btn` buttons toggle two
  `.tab-panel` divs (`#panel-modules`, `#panel-recipes`), each with its own
  `<table>`/`<tbody>` (`#tbl-modules`/`#tbody-modules`,
  `#tbl-recipes`/`#tbody-recipes`) and its own "no results" message. Only
  the modules panel has the stability/security checkboxes (neither concept
  applies to recipes); **both** panels have a name filter and a category filter.
- The column-sort mechanics (`sortTable`/`cellVal`/header-click-wiring) are
  factored into a `makeSorter(theadSel, tbodySel)` factory shared by both
  tables — that logic is genuinely identical. The **filter** logic is
  deliberately *not* shared: `applyModulesFilter()` does
  name+stability+security+category, `applyRecipesFilter()` does name+category —
  forcing the simpler recipes filter through the modules' shape would add
  pointless conditionals to already-tested code.
- The modules table defaults to sorting by usage (col 4) descending on load
  (`modulesSorter.sortTable(4, 'num')`), matching the row order the Python side
  already produced. The recipes table defaults to Packagist downloads (col 3)
  descending (`recipesSorter.sortTable(3, 'num')`). The Categories column is
  col 5 in both tables — appended last, so these indices are unchanged.

Design notes for `render_html.py`:
- CSS and JS are stored as regular Python string variables (not f-strings) to
  avoid the need to escape every `{` and `}`. Only the final HTML assembly
  uses f-strings for the handful of variable substitutions.
- All cell values used for sorting are stored in `data-val` attributes on each
  `<td>`. Sorting never reads rendered text — it reads the raw value:
  - Label (col 0): plain text (for both sorting and the name filter) — the
    `description` sub-line is rendered inside the same `<td>` but is **not**
    part of `data-val`, so it never affects sorting or filtering (the machine
    name is not its own column — it's the link's `title` attribute)
  - Version (col 1): raw semver string; `""` for missing (sorts to bottom)
  - Released (col 2): `YYYY-MM-DD` string; `""` for `"—"` (sorts to bottom)
  - Security coverage (col 3): `"1"`/`"0"`, sorted numerically
  - Drupal.org usage (col 4): integer as string; `""` for unknown (treated as -Infinity)
  - Categories (col 5): the categories joined into one string; sorts as text
- Recipe table `data-val` attributes:
  - Label (col 0): plain text (description sub-line excluded, same as modules)
  - Version (col 1): raw semver string; `""` for missing
  - Released (col 2): `YYYY-MM-DD` string; `""` for `"—"`
  - Packagist downloads (col 3): integer as string; `""` for unknown (treated as -Infinity)
  - Packagist stars (col 4): integer as string; `""` for unknown (treated as -Infinity)
  - Categories (col 5): the categories joined into one string; sorts as text
- Sort state is tracked with `sortCol` (column index, -1 = none) and
  `sortDir` (1 = asc, -1 = desc). First click on a text column → ascending;
  first click on a numeric column → descending. Re-click reverses. Default
  sort on load is column 4 (usage) for modules, column 3 (downloads) for
  recipes — both descending.
- `html.escape()` is used on all row values to prevent XSS from any
  unexpected characters in API responses.
- Security coverage uses three constants defined locally in each renderer
  (not shared with `drupal_ai_dependents.py`, which doesn't render anything):
  - `SECURITY_COVERED_STABLE_HTML` / `SECURITY_COVERED_STABLE_MD` — filled shield
    (`<img src="images/shield-icon-black.svg" style="opacity:0.5">`) for covered + stable.
    HTML uses the img tag directly; Markdown uses the same img tag (renders in GitHub MD).
  - `SECURITY_COVERED_PRERELEASE_HTML` / `SECURITY_COVERED_PRERELEASE_MD` — outline shield
    for covered + pre-release. HTML uses an inline `<svg>` (stroke only, no fill, `style="opacity:0.5"`);
    Markdown uses an `<img>` with a self-contained SVG data URI (`style="opacity:0.5"`).
  - `SECURITY_NOT_COVERED_EMOJI` (🚫) — unchanged, used by both renderers.
  The choice between filled vs. outline is made per-row by comparing `stability == "stable"`.
  `data-security` on `<tr>` remains `"covered"` / `"not-covered"` — both shield variants
  belong to the same filter state.

---

## Categorization

Every module and recipe carries a `categories` list (multiple allowed) — see
the JSON schema below. Classification is a deterministic, rule-based keyword
matcher living entirely in `drupal_ai_dependents.py` (the *Categorization*
section, right after `detect_stability`). No network, no AI, no new file: an
earlier draft put this in a standalone `categorize.py`, but it was folded into
the main script so there's a single place that owns the taxonomy.

**Taxonomy — 18 categories** (`CATEGORIES` constant, canonical display order):
Tool, Cloud Providers, Local Providers, Editorial, Content, Search, Chat,
Automation, Agents, Analytics, Media, Vector Database, SEO & Metadata,
Translation, Safety & Governance, Accessibility, Developer Tools,
Evaluation & Testing. `render_html.py` keeps a local `CATEGORY_ORDER` mirroring
this (like `STABILITY_ORDER`/`SECURITY_ORDER`) so the renderer stays
import-free; anything not in that list still renders, it just sorts last.

**`detect_categories(label, description, machine_name)`:**
- Builds `name_hay` (bare machine name + label) and `full_hay` (+ description),
  both `html.unescape()`d and lowercased.
- `_OVERRIDES[bare_machine_name]` short-circuits everything (used verbatim) —
  for the null-description packages (`drup_aid`, `ai_audio_translate`,
  `quiz_questions_by_eca_and_ai`) and stubborn cases (`agui`, `ai_context`, …).
- Otherwise, for each category in `CATEGORIES` order, it matches when no
  `_EXCLUDES` term appears in `full_hay` **and** (a `_KEYWORDS_NAME` term hits
  `name_hay` **or** a `_KEYWORDS_ANY` term hits `full_hay`). Iterating in
  canonical order keeps the returned list stable and deduped.
- Returns `["Uncategorized"]` (`UNCATEGORIZED`) when nothing matches.

**Why two keyword tables:** `_KEYWORDS_ANY` matches anywhere; `_KEYWORDS_NAME`
matches only in the name. Noisy categories (Agents, Providers) use NAME-scoping
so a Tool whose description merely says "for use with AI agents" isn't tagged
Agents — this is exactly why `"ai agent"` is **not** an ANY keyword for Agents
(it re-introduced that over-tagging during development and was removed; rely on
the name-scoped `"agent"`). `_EXCLUDES` keeps vector-DB providers and local
runtimes (Ollama/vLLM/llama.cpp/LM Studio/AnythingLLM/browser) out of
`Cloud Providers` — a provider is cloud **or** local **or** a VDB, not several.

**Running it:** `apply_categories(payload)` is called automatically right before
the JSON is written on a normal `--json` run, so fresh output is always
categorized. `--categorize FILE` is a network-free fast path
(`recategorize_file()`) that re-derives categories on an existing `results.json`
**in place** (no `-o`) and prints a per-category summary + the `Uncategorized`
list to stderr — the loop for tuning the keyword tables without repeating the
~20-25 min crawl. After tuning, re-render with `render_md.py` / `render_html.py`.

**Renderers:** both show a Categories column (appended as the **last** column so
the existing `data-col` indices and initial-sort calls are untouched).
`render_md.py` joins the list comma-separated (`_categories_cell`, escaped like
`_label_cell`). `render_html.py` renders `.cat-pill` spans, puts a
pipe-delimited `data-categories` on each `<tr>` (the recipe rows' first data
attribute), and adds a per-tab category checkbox filter (`.cat-cb`, scoped by
`#panel-modules` / `#panel-recipes`) that is **match-any**: a row stays visible
while at least one of its categories is still checked. Because a package can
have several categories (unlike single-valued stability/security), this filter
deliberately differs from — and is not shared with — the stability/security
filter logic.

## JSON output and rendering pipeline

`drupal_ai_dependents.py` writes JSON and nothing else — `--json FILE`
writes it to a file; omitting `--json` prints the same JSON to stdout. This
is deliberate: an earlier version also rendered markdown/HTML directly
(`-o`/`--output` and `--html` flags, plus an inline `render_html(rows)`
function), but that inline renderer drifted out of sync with
`render_html.py` every time the real renderer gained a feature (it never
got recipe support, stability badges, or filter checkboxes). Removing it
means there's exactly one place each output format is rendered.

The payload is written after the sort step:

```json
{
  "generated":         "2026-06-22",
  "drupal_versions":   [10, 11],
  "modules": [
    {
      "machine_name":     "drupal/ai_provider_openai",
      "label":            "OpenAI Provider",
      "description":      "Adds OpenAI as a provider for the AI module.",
      "url":              "https://www.drupal.org/project/ai_provider_openai",
      "version":          "1.2.1",
      "release_date":     "2026-02-25",
      "usage":            13133,
      "security_covered": true,
      "stability":        "stable",
      "categories":       ["Cloud Providers"]
    }
  ],
  "recipes": [
    {
      "machine_name": "drupal/ai_recipe_image_classification",
      "label":        "AI Image Classification recipe",
      "description":  "Configures automatic image classification using AI.",
      "url":          "https://www.drupal.org/project/ai_recipe_image_classification",
      "version":      "1.1.0",
      "release_date": "2026-02-12",
      "stability":    "stable",
      "downloads":    1234,
      "stars":        56,
      "categories":   ["Media", "Search"]
    }
  ]
}
```

Recipe rows have no `usage` or `security_covered` keys at all — not `null`,
simply absent — since both concepts genuinely don't apply (see "What the
script does" above). Recipe rows DO have `downloads` and `stars` keys (integers or `null`) from
Packagist's stats API — both come from a single request per recipe. Module rows
have neither key. `recipe_rows` is sorted by `downloads` descending (`None`/0
last), then alphabetically by `label` as a tiebreaker:
`recipe_rows.sort(key=lambda r: (-(r["downloads"] or 0), r["label"].lower()))`

- `machine_name` is the composer.json `name` field for the package (e.g.
  `drupal/ai_provider_openai`), taken from the same p2 response already
  fetched for verification — sourced from `best.get("name")` in `get_p2_info()`,
  stored as `composer_name` internally and copied into the row's `machine_name`
  key. There is no separate "bare machine name" field in the JSON output; the
  bare name (e.g. `ai_provider_openai`) only exists as a local variable used
  to build URLs and fetch requests.
- `label` is the `name:` key from the module's `*.info.yml`, fetched by
  `get_module_label_and_description()` (recipes: `get_recipe_label_and_description()`
  reading `recipe.yml`). Falls back to `human_name(machine_name)` (title-cased)
  when the file can't be fetched or parsed.
- `description` is the `description:` key from the **same** `*.info.yml` /
  `recipe.yml` parse (no extra request), or `null` when that key is absent.
  Present on **both** module and recipe rows. There is no fallback source —
  the meta-tag approach considered during development was dropped in favour of
  using only the info.yml/recipe.yml description.
- `security_covered` is a bool from `get_usage_and_security()`, derived from
  `field_security_advisory_coverage == "covered"` on the same Drupal.org API
  node already fetched for usage counts.
- `stability` is computed by `detect_stability(version)` in the main script using
  `_STABILITY_RE = re.compile(r'-(?P<level>alpha|beta|rc|dev)', re.IGNORECASE)`.
  Empty/missing version strings default to `"stable"`.
- `categories` is a **list** (multiple allowed) computed by
  `detect_categories(label, description, machine_name)` and applied to the whole
  payload by `apply_categories(payload)` just before the JSON is written.
  Present on **both** module and recipe rows; `["Uncategorized"]` when no rule
  matches. See the Categorization section below.

Old `results.json` files without the `stability` field are handled gracefully
in `render_html.py` via `r.get("stability", "stable")`, files predating the
`description` field via `r.get("description")`, and files predating the
`categories` field via `r.get("categories", [])` (renders no category cell) —
all in both renderers. Older files predating the
`label`/`machine_name`(composer-name)/`security_covered` fields are
**not** compatible with the current renderers — re-run the main script to
regenerate `results.json` before re-rendering.

Files predating the `"recipes"` key entirely (from before recipe support
was added) **are** compatible — both renderers read it via
`payload.get("recipes", [])`, rendering an empty Recipes section/tab rather
than crashing.

Recommended workflow:
```bash
python3 drupal_ai_dependents.py --json results.json       # slow, once (output already categorized)
python3 drupal_ai_dependents.py --categorize results.json # fast — only when tuning category rules
python3 render_md.py results.json -o results.md           # fast, re-run anytime
python3 render_html.py results.json -o results.html       # fast, re-run anytime
```

---

## File inventory

| File | Purpose |
|------|---------|
| `drupal_ai_dependents.py` | Main script — data collection, candidate verification, and categorization (`detect_categories` / `apply_categories` / `--categorize`) |
| `render_md.py` | Markdown renderer — reads `results.json`, outputs markdown table (incl. Categories column) |
| `render_html.py` | HTML renderer — reads `results.json`, outputs HTML with stability/security/category filters |
| `README.md` | User-facing documentation (usage, limitations, how it works) |
| `CLAUDE.md` | This file — context for future Claude Code sessions |

Recipe support (modules-and-recipes) was added entirely within these four
files — no new files were introduced. The categorization system was likewise
added within `drupal_ai_dependents.py` and the two renderers (an early
standalone `categorize.py` draft was folded into the main script) — still no
new files.
