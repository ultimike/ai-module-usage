#!/usr/bin/env python3
"""
Find all Drupal modules and recipes with a hard dependency on drupal/ai
(https://www.drupal.org/project/ai) and write the results as JSON. This
script only collects and verifies data — render the output with
render_md.py (markdown) or render_html.py (sortable/filterable HTML).

Module candidate discovery (two sources, union-merged):
  1. drupal.org/project/ai/ecosystem  — curated AI ecosystem listing
  2. packages.drupal.org search "ai"  — package name/description search

Module dependency verification (authoritative, reads composer.json directly):
  packages.drupal.org/files/packages/8/p2/drupal/{name}.json
  Confirms type=drupal-module AND drupal/ai is in the require field.
  Also supplies version, Drupal core constraint, and release datestamp.

Module additional data:
  updates.drupal.org           — release date fallback (when p2 has no datestamp)
  www.drupal.org/api-d7/node.json — usage/install count + security coverage

Recipes are verified separately, against the regular Packagist registry
(packages.drupal.org doesn't carry them at all) — see get_recipe_info() and
CLAUDE.md's "Data sources" section for details.

Usage:
  python3 drupal_ai_dependents.py [--json FILE]

  # Write JSON, then render separately (recommended):
  python3 drupal_ai_dependents.py --json results.json
  python3 render_md.py results.json -o results.md
  python3 render_html.py results.json -o results.html
"""

# Python's standard library modules — no composer/npm needed.
# Think of these like PHP's built-in extensions (json_decode, preg_match, etc.)
import argparse           # parses command-line flags like --json
import json               # like PHP's json_decode() / json_encode()
import re                 # like PHP's preg_match() / preg_replace()
import sys                # access to stdin/stdout/stderr and script exit
import threading          # Lock for thread-safe rate limiting
import time               # like PHP's sleep() and microtime()
import urllib.error       # HTTP error types thrown by urllib (like curl errors)
import urllib.parse       # URL building — like PHP's http_build_query()
import urllib.request     # makes HTTP requests — like PHP's curl or file_get_contents()
import xml.etree.ElementTree as ET  # XML parser — like PHP's SimpleXMLElement
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone  # date handling — like PHP's DateTime


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# URL templates. The {name} placeholder is filled in later with str.format(),
# which works like sprintf() in PHP: P2_URL.format(name="ai_agents")
ECOSYSTEM_URL        = "https://www.drupal.org/project/ai/ecosystem"
PACKAGES_SEARCH_URL  = "https://packages.drupal.org/8/search.json"
P2_URL               = "https://packages.drupal.org/files/packages/8/p2/drupal/{name}.json"
RELEASE_HISTORY_URL  = "https://updates.drupal.org/release-history/{name}/current"
DRUPAL_API_URL       = "https://www.drupal.org/api-d7/node.json"
DRUPAL_PROJECT_URL   = "https://www.drupal.org/project/{name}"
INFO_YML_URL         = "https://git.drupalcode.org/project/{name}/-/raw/{version}/{name}.info.yml"

# Recipes (Drupal projects of type "drupal-recipe") are NOT published to
# packages.drupal.org at all — confirmed by a 404 on P2_URL for any recipe.
# They ARE published to the regular Packagist registry, in the same
# Composer v2 p2 format, so recipe verification uses a second registry.
RECIPE_SEARCH_URL    = "https://packagist.org/search.json"
RECIPE_P2_URL        = "https://repo.packagist.org/p2/drupal/{name}.json"
RECIPE_P2_DEV_URL    = "https://repo.packagist.org/p2/drupal/{name}~dev.json"
RECIPE_YML_URL       = "https://git.drupalcode.org/project/{name}/-/raw/{ref}/recipe.yml"
CURATED_RECIPES_URL  = "https://git.drupalcode.org/project/ai_dashboard/-/raw/1.0.x/ai_dashboard_recommended_recipes.yml?ref_type=heads"

# A Python `set` is like a PHP array used as a lookup table (array_flip'd),
# where only unique values matter and order doesn't. Membership checks are O(1).
TARGET_VERSIONS = {10, 11}

# Polite delays — applied BEFORE each request.
# time.sleep(3.0) pauses execution for 3 seconds, like PHP's sleep(3),
# except it accepts fractions of a second.
ECOSYSTEM_PAGE_DELAY = 3.0   # www.drupal.org ecosystem pages
PACKAGES_DELAY       = 3.0   # packages.drupal.org (search pages + p2 files)
RELEASE_DELAY        = 3.0   # updates.drupal.org (date fallback only)
DRUPAL_API_DELAY     = 3.0   # www.drupal.org JSON API
GITLAB_DELAY         = 3.0   # git.drupalcode.org (info.yml / recipe.yml label fetch)
PACKAGIST_DELAY      = 3.0   # packagist.org (recipe search) + repo.packagist.org (recipe p2 files)

MAX_RETRIES   = 4    # how many times to retry a failed API call before giving up
RETRY_BACKOFF = 2.0  # starting wait in seconds; doubles after each retry

# Request headers sent with every HTTP call. The dict literal here is like
# PHP's associative array: ["User-Agent" => "..."]
HEADERS = {
    "User-Agent": "drupal-ai-dependents-script/1.0 (research tool)"
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

# Function signatures in Python use type hints after the colon.
# `str` = string, `dict` = associative array, `None` = null,
# `dict | None` = a union type (like PHP 8's string|null).
# The return type after `->` shows what the function returns.
# `tuple[int, bytes, dict]` means it returns three values at once —
# like returning an array from a PHP function and list()ing it on the other end.
def _http_get(url: str, params: dict | None = None) -> tuple[int, bytes, dict]:
    """Make a GET request and return (status_code, body_bytes, headers_dict).

    The leading underscore in _http_get is a Python convention meaning
    "private/internal" — like protected in PHP. It signals this function
    is a helper not meant to be called from outside this file.

    Network-level errors (timeouts, SSL failures, connection resets) are
    retried up to MAX_RETRIES times with exponential backoff before raising.
    """
    # If query parameters were provided, append them to the URL.
    # urllib.parse.urlencode({"s": "ai", "page": 2}) → "s=ai&page=2"
    # This is equivalent to PHP's http_build_query().
    if params:
        url = url + "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, headers=HEADERS)

    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read(), dict(resp.headers)

        except urllib.error.HTTPError as exc:
            # HTTPError is raised for 4xx/5xx responses (unlike PHP's curl,
            # which just sets an error code). We catch it and return the status
            # code normally so callers can handle e.g. 404 without a crash.
            # `b""` is an empty byte string (the body was not retrieved).
            # HTTPError is not retryable — a 404 won't become a 200 on retry.
            return exc.code, b"", dict(exc.headers) if exc.headers else {}

        except urllib.error.URLError as exc:
            # URLError covers network-level failures: timeouts, SSL errors,
            # DNS failures, connection resets. These are often transient, so
            # we retry with exponential backoff before giving up.
            # HTTPError is a subclass of URLError, but caught above first.
            if attempt + 1 < MAX_RETRIES:
                wait = RETRY_BACKOFF * (2 ** attempt)  # 2s, 4s, 8s …
                print(
                    f"  [retry {attempt + 1}/{MAX_RETRIES}] network error"
                    f" — waiting {wait:.0f}s ({exc})",
                    file=sys.stderr,
                )
                time.sleep(wait)
            else:
                raise RuntimeError(f"Request failed for {url}: {exc}") from exc


def _drupal_api_get(params: dict) -> dict | None:
    """Call the Drupal.org JSON API, retrying on 503 (rate-limit) responses.

    Returns the decoded JSON as a dict, or None if the request ultimately fails.
    `dict | None` as a return type is Python 3.10+ union syntax, equivalent
    to PHP 8's ?array or array|null.
    """
    backoff = RETRY_BACKOFF  # local copy so we can double it each iteration

    # range(MAX_RETRIES) produces [0, 1, 2, 3] — like PHP's for ($i = 0; $i < 4; $i++)
    for attempt in range(MAX_RETRIES):
        try:
            status, body, hdrs = _http_get(DRUPAL_API_URL, params)
        except RuntimeError:
            return None

        if status == 503:
            # 503 means the server is rate-limiting us. Check if it told us
            # how long to wait via the Retry-After header.
            # dict.get() is like PHP's $array['key'] ?? null — returns None
            # if the key doesn't exist, avoiding a KeyError (like PHP's
            # Undefined index notice).
            raw = hdrs.get("Retry-After") or hdrs.get("retry-after")

            # Use the server's suggested wait if given, otherwise use our
            # exponential backoff value. float() converts a string like "30"
            # to the number 30.0, like PHP's (float) cast.
            wait = float(raw) if raw else backoff

            print(f"  [retry {attempt+1}/{MAX_RETRIES}] 503 — waiting {wait:.0f}s",
                  file=sys.stderr)
            time.sleep(wait)
            backoff *= 2  # double the backoff for next time: 2 → 4 → 8 → 16
            continue      # jump back to the top of the for loop

        if status != 200:
            return None  # unrecoverable error (404, 500, etc.)

        # json.loads() is exactly PHP's json_decode($body, true) —
        # it parses a JSON string into a Python dict (associative array).
        return json.loads(body)

    # If we exhausted all retries, give up and return None.
    return None


# ---------------------------------------------------------------------------
# Candidate discovery — source 1: ecosystem page
# ---------------------------------------------------------------------------

# Compile the regex once at module load, rather than recompiling it on every
# call. This is like storing a compiled pattern in a static variable in PHP.
# The pattern captures the project machine name from links like:
#   href="/project/ai_image_alt_text"
_PROJECT_HREF_RE = re.compile(r'href="/project/([a-z0-9_]+)"')


def _scrape_ecosystem_page(page: int) -> tuple[list[str], bool]:
    """Scrape one page of the ecosystem listing.

    Returns a tuple of (list_of_machine_names, has_more_pages).
    `list[str]` is a typed list — like PHP's array where every value is a string.
    `bool` is True/False.
    """
    # Only add the `page` query parameter for pages after the first.
    # The conditional expression `x if condition else y` is Python's
    # ternary operator, equivalent to PHP's `condition ? x : y`.
    status, body, _ = _http_get(ECOSYSTEM_URL, {"page": page} if page > 0 else None)

    if status != 200:
        # Return an empty list and False to signal "nothing found, stop paging".
        # `[]` is an empty list, like PHP's [].
        return [], False

    # Decode the raw bytes to a UTF-8 string, replacing any invalid characters.
    # This is like PHP's mb_convert_encoding() or utf8_decode().
    text = body.decode("utf-8", errors="replace")

    # A `set` here acts as a "seen" tracker to deduplicate results in O(1).
    # Think of it as array_flip(array_unique($names)) in PHP.
    seen, names = set(), []

    # finditer() works like preg_match_all() in PHP — it returns all matches.
    # Each match `m` is an object; m.group(1) is the first capture group,
    # equivalent to PHP's $matches[1][0].
    for m in _PROJECT_HREF_RE.finditer(text):
        n = m.group(1)
        # `not in` is Python's equivalent of !in_array() or !isset($seen[$n]).
        if n not in seen and n != "ai":
            seen.add(n)     # add to set (like $seen[$n] = true in PHP)
            names.append(n) # append to list (like $names[] = $n in PHP)

    # Check if the page HTML contains a link to the next page number.
    # re.search() is like preg_match() — returns a match object or None.
    # bool() converts it to True/False, like (bool) in PHP.
    has_more = bool(re.search(r'[?&]page=' + str(page + 1), text))

    return names, has_more


def get_ecosystem_candidates() -> list[str]:
    """Paginate through all ecosystem pages and return unique machine names."""
    all_names, seen = [], set()
    page = 0

    # Python's `while True` with a `break` is a common pattern for loops
    # where the exit condition is checked mid-loop, like PHP's do/while.
    while True:
        time.sleep(ECOSYSTEM_PAGE_DELAY)
        print(f"    Page {page} …", file=sys.stderr)

        # Unpack the tuple returned by _scrape_ecosystem_page into two variables.
        # Equivalent to PHP's [$names, $has_more] = _scrape_ecosystem_page($page);
        names, has_more = _scrape_ecosystem_page(page)

        for n in names:
            if n not in seen:
                seen.add(n)
                all_names.append(n)

        if not has_more:
            break  # exit the while loop

        page += 1  # equivalent to $page++ in PHP

    return all_names


# ---------------------------------------------------------------------------
# Candidate discovery — source 2: packages.drupal.org search
# ---------------------------------------------------------------------------

def get_search_candidates(query: str = "ai") -> list[str]:
    """Paginate packages.drupal.org search and return machine names.

    `query: str = "ai"` defines a default parameter value, just like
    PHP's function get_search_candidates(string $query = 'ai').
    """
    all_names, seen = [], set()

    # `str | None` means this variable can hold a string or None (null).
    # We start with the base search URL and will replace it with the
    # server-provided `next` URL on subsequent pages.
    url: str | None = PACKAGES_SEARCH_URL
    params: dict | None = {"s": query, "per_page": 100}

    while url:
        time.sleep(PACKAGES_DELAY)

        # Pass params on the first request; subsequent requests use the
        # complete `next` URL which already has parameters embedded.
        try:
            status, body, _ = _http_get(url, params)
        except RuntimeError as exc:
            print(f"  Warning: packages.drupal.org search failed: {exc}",
                  file=sys.stderr)
            print("  Continuing with ecosystem candidates only.", file=sys.stderr)
            break
        params = None  # clear params — the next URL already includes them

        if status != 200 or not body:
            break

        # json.loads() decodes the JSON response body into a Python dict.
        data = json.loads(body)

        # data.get("results", []) safely retrieves the "results" key,
        # defaulting to an empty list if missing — like PHP's
        # $data['results'] ?? []
        for pkg in data.get("results", []):
            name = pkg.get("name", "")

            # We only want packages in the drupal/* namespace.
            # str.startswith() is like PHP's str_starts_with().
            if name.startswith("drupal/"):
                # str.removeprefix() strips a known prefix — like PHP's
                # substr($name, strlen('drupal/')) or ltrim($name, 'drupal/')
                # but safer (only removes it if actually present).
                machine = name.removeprefix("drupal/")
                if machine not in seen and machine != "ai":
                    seen.add(machine)
                    all_names.append(machine)

        # The API returns the next page URL in a "next" key, or omits it on
        # the last page. `or None` converts a falsy empty string to None,
        # which will stop the while loop. Like PHP's $url = $data['next'] ?: null;
        url = data.get("next") or None

    return all_names


# ---------------------------------------------------------------------------
# Candidate discovery — recipes, source 1: Packagist type search
# ---------------------------------------------------------------------------

def get_recipe_search_candidates() -> list[str]:
    """Paginate the regular Packagist registry for drupal-recipe packages.

    Recipes aren't published to packages.drupal.org at all, so this queries
    the main Packagist instead — `type=drupal-recipe` alone returns ~670
    packages regardless of AI-relatedness, which would roughly double this
    script's run time to verify. Adding `q="ai"` narrows that to ~60 while
    still catching every recipe we need (confirmed live, including
    drupal/drupal_cms_ai). This accepts the same "could miss a non-'ai'-named
    dependent" tradeoff already made by get_search_candidates() for modules —
    false positives are expected and filtered out at verification.
    """
    all_names, seen = [], set()

    url: str | None = RECIPE_SEARCH_URL
    params: dict | None = {"q": "ai", "type": "drupal-recipe", "per_page": 100}

    while url:
        time.sleep(PACKAGIST_DELAY)

        try:
            status, body, _ = _http_get(url, params)
        except RuntimeError as exc:
            print(f"  Warning: Packagist recipe search failed: {exc}",
                  file=sys.stderr)
            print("  Continuing without Packagist recipe candidates.", file=sys.stderr)
            break
        params = None  # clear params — the next URL already includes them

        if status != 200 or not body:
            break

        data = json.loads(body)

        for pkg in data.get("results", []):
            name = pkg.get("name", "")
            if name.startswith("drupal/"):
                machine = name.removeprefix("drupal/")
                if machine not in seen:
                    seen.add(machine)
                    all_names.append(machine)

        url = data.get("next") or None

    return all_names


# ---------------------------------------------------------------------------
# Candidate discovery — recipes, source 2: curated AI recipe list
# ---------------------------------------------------------------------------

# Matches a `machineName:` value anywhere in the curated YAML file, e.g.
# `  machineName: ai_recipe_image_classification`. We don't parse the YAML
# structure at all (no PyYAML in this project, same as _INFO_NAME_RE's
# approach for info.yml) — every name extracted here still goes through full
# p2 verification against Packagist, so a loose regex is safe.
_CURATED_MACHINE_NAME_RE = re.compile(r'machineName:\s*(\S+)')


def get_curated_recipe_candidates() -> list[str]:
    """Fetch the AI Dashboard module's hand-maintained list of AI recipes.

    This is a supplementary source, not authoritative — it's maintained by
    a third party and isn't guaranteed exhaustive or even currently accurate.
    It's useful because it catches at least one real AI recipe
    (drupal/drupal_cms_ai) that doesn't appear on the ecosystem page at all.
    Every name returned here still gets verified via get_recipe_info() like
    any other candidate.
    """
    try:
        status, body, _ = _http_get(CURATED_RECIPES_URL)
    except RuntimeError as exc:
        print(f"  Warning: curated recipe list fetch failed: {exc}", file=sys.stderr)
        return []

    if status != 200 or not body:
        print(f"  Warning: curated recipe list returned HTTP {status}", file=sys.stderr)
        return []

    text = body.decode("utf-8", errors="replace")

    seen, names = set(), []
    for m in _CURATED_MACHINE_NAME_RE.finditer(text):
        n = m.group(1)
        if n not in seen:
            seen.add(n)
            names.append(n)

    return names


# ---------------------------------------------------------------------------
# Dependency verification — packages.drupal.org p2
# ---------------------------------------------------------------------------

def _core_is_d10_d11(constraint: str) -> bool:
    """Check whether a drupal/core Composer version constraint covers D10 or D11.

    For example: "^10.3 || ^11" → True, "^8 || ^9" → False.
    A missing or wildcard ("*") constraint is treated as compatible because
    the packages.drupal.org/8 repository only serves modern Drupal packages.
    """
    if not constraint or constraint == "*":
        return True

    # `any()` is like PHP's array_filter() used as a boolean check —
    # it returns True as soon as one element is truthy, short-circuiting
    # the rest. Here we check if any TARGET_VERSION number (10, 11) appears
    # as a whole word (\b is a word boundary) in the constraint string.
    # rf"\b{v}\b" is a raw f-string — the r prefix stops Python from
    # interpreting backslashes, and f allows {v} substitution.
    # Result: rf"\b{10}\b" → the regex pattern r"\b10\b"
    return bool(any(re.search(rf"\b{v}\b", constraint) for v in TARGET_VERSIONS))


def _select_best_ai_dependent_version(
    versions: list[dict], required_type: str
) -> dict | None:
    """Filter a p2 version list down to the newest version that matches
    `required_type` and has a Drupal 10/11-compatible drupal/ai dependency.

    Shared by get_p2_info() (modules, packages.drupal.org) and
    get_recipe_info() (recipes, Packagist) — the verification rules are
    identical, only the registry URL and the required `type` differ.

    `versions` is the raw p2 version list, newest-first, each entry the full
    composer.json contents for that release. The `type` field is stable
    across versions, so checking only the first (latest) entry is sufficient.

    Returns the chosen version dict (still containing the full raw
    composer.json fields for that release), or None if nothing qualifies.
    """
    if not versions or versions[0].get("type") != required_type:
        return None

    # Collect all versions that satisfy requirements, then pick the best one.
    candidates = []
    for v in versions:
        require = v.get("require") or {}
        if "drupal/ai" not in require:
            continue
        core_constraint = require.get("drupal/core", "")
        if not _core_is_d10_d11(core_constraint):
            continue
        candidates.append(v)

    if not candidates:
        return None

    # Prefer the newest stable release; fall back to the newest pre-release
    # if no stable version exists (e.g. module is still in beta).
    return next(
        (v for v in candidates if detect_stability(v.get("version", "")) == "stable"),
        candidates[0],
    )


def get_p2_info(machine_name: str) -> dict | None:
    """Fetch and parse a module's Composer p2 metadata from packages.drupal.org.

    The p2 format is Composer's v2 repository format. Each package gets its own
    JSON file listing every published version along with its full composer.json
    contents — so we can read `require`, `type`, and `extra` directly.

    Returns a dict with version/date/constraints, or None if the module fails
    any of our three checks (exists, is a module, requires drupal/ai for D10/11).
    """
    url = P2_URL.format(name=machine_name)
    try:
        status, body, _ = _http_get(url)
    except RuntimeError:
        return None

    # A 404 means this package simply doesn't exist on packages.drupal.org.
    if status != 200 or not body:
        return None

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        # Malformed JSON — skip this package.
        return None

    # The p2 JSON structure is: {"packages": {"drupal/ai_agents": [{...}, {...}]}}
    # Versions are listed newest-first. This is equivalent to PHP's:
    # $versions = $data['packages']["drupal/$machine_name"] ?? [];
    versions = data.get("packages", {}).get(f"drupal/{machine_name}", [])

    # Type check excludes recipes (`drupal-recipe`), profiles, distributions —
    # those are handled by the separate get_recipe_info() pipeline.
    best = _select_best_ai_dependent_version(versions, "drupal-module")
    if best is None:
        return None

    require = best.get("require") or {}
    core_constraint = require.get("drupal/core", "")
    datestamp = (best.get("extra") or {}).get("drupal", {}).get("datestamp")
    date = (
        datetime.fromtimestamp(int(datestamp), tz=timezone.utc).strftime("%Y-%m-%d")
        if datestamp
        else None
    )
    return {
        "version":         best.get("version", ""),
        "date":            date,
        "ai_constraint":   require["drupal/ai"],
        "core_constraint": core_constraint,
        # The package's own composer.json "name" field, e.g. "drupal/ai_agents" —
        # already present in the p2 payload, so no extra request is needed.
        "composer_name":   best.get("name", ""),
    }


def _fetch_p2_with_delay(machine_name: str):
    """Sleep then fetch p2 info — designed to run in a worker thread."""
    time.sleep(PACKAGES_DELAY)
    return machine_name, get_p2_info(machine_name)


# ---------------------------------------------------------------------------
# Dependency verification — recipes: repo.packagist.org p2
# ---------------------------------------------------------------------------

def get_recipe_info(machine_name: str) -> dict | None:
    """Fetch and parse a recipe's Composer p2 metadata from Packagist.

    Recipes aren't on packages.drupal.org at all (confirmed: 404 on P2_URL
    for any recipe), so this hits the regular Packagist registry instead —
    same Composer v2 p2 format, just a different host and required `type`.

    Unlike packages.drupal.org's p2 files, Packagist's have a real ISO-8601
    `time` field on every version, so there's no datestamp workaround and
    no need for an updates.drupal.org date fallback at all.

    Composer splits stable/tagged releases (this URL) from branch/dev
    snapshots into a separate "~dev" p2 file — confirmed live: a recipe
    with only a "1.x-dev" release returns an EMPTY version list here, with
    its actual data only in RECIPE_P2_DEV_URL. Both are fetched and merged
    so dev-only recipes (no tagged release yet) still verify correctly.

    Returns a dict with version/date/constraints, or None if the recipe
    fails verification (doesn't exist, isn't a recipe, or doesn't have a
    Drupal 10/11-compatible drupal/ai dependency).
    """
    versions = []
    for url in (RECIPE_P2_URL.format(name=machine_name),
                RECIPE_P2_DEV_URL.format(name=machine_name)):
        try:
            status, body, _ = _http_get(url)
        except RuntimeError:
            continue

        if status != 200 or not body:
            continue

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            continue

        versions += data.get("packages", {}).get(f"drupal/{machine_name}", [])

    best = _select_best_ai_dependent_version(versions, "drupal-recipe")
    if best is None:
        return None

    require = best.get("require") or {}
    core_constraint = require.get("drupal/core", "")
    raw_time = best.get("time")
    date = (
        datetime.fromisoformat(raw_time).strftime("%Y-%m-%d")
        if raw_time
        else None
    )
    return {
        "version":         best.get("version", ""),
        "date":            date,
        "ai_constraint":   require["drupal/ai"],
        "core_constraint": core_constraint,
        "composer_name":   best.get("name", ""),
    }


def _fetch_recipe_info_with_delay(machine_name: str):
    """Sleep then fetch recipe p2 info — designed to run in a worker thread."""
    time.sleep(PACKAGIST_DELAY)
    return machine_name, get_recipe_info(machine_name)


# ---------------------------------------------------------------------------
# Module label — git.drupalcode.org (reads the *.info.yml `name` key)
# ---------------------------------------------------------------------------

# Matches a top-level `name:` key in a .info.yml file, e.g. `name: AI Core`.
# Anchored with MULTILINE so `^`/`$` match line boundaries, not the whole file.
_INFO_NAME_RE = re.compile(r'^name:\s*(.+?)\s*$', re.MULTILINE)


def get_module_label(machine_name: str, version: str) -> str | None:
    """Fetch a module's human-readable label from its {name}.info.yml file.

    Reads the raw file straight from git.drupalcode.org (Drupal.org's GitLab
    instance) at the tag matching the module's release version — this is the
    only place the `name:` key (the actual project label) lives; it isn't
    part of composer.json or any JSON API response.

    Returns None if the file can't be fetched or has no `name:` key.
    """
    url = INFO_YML_URL.format(name=machine_name, version=version)
    try:
        status, body, _ = _http_get(url)
    except RuntimeError:
        return None
    if status != 200 or not body:
        return None

    match = _INFO_NAME_RE.search(body.decode("utf-8", errors="replace"))
    if not match:
        return None

    label = match.group(1).strip()
    # Strip a single layer of quotes — YAML allows name: 'X' or name: "X".
    if len(label) >= 2 and label[0] == label[-1] and label[0] in "'\"":
        label = label[1:-1]
    return label or None


def _fetch_label_with_delay(machine_name: str, version: str):
    """Sleep then fetch the info.yml label — designed to run in a worker thread."""
    time.sleep(GITLAB_DELAY)
    return machine_name, get_module_label(machine_name, version)


# ---------------------------------------------------------------------------
# Recipe label — git.drupalcode.org (reads the recipe.yml `name` key)
# ---------------------------------------------------------------------------

_DEV_SUFFIX_RE = re.compile(r'-dev$')


def _git_ref_for_version(version: str) -> str:
    """Convert a Composer dev-version string to the git ref GitLab actually
    serves raw files from.

    Composer's "-dev" suffix (e.g. "1.x-dev") marks a branch alias, not a
    real git tag — GitLab has no "1.x-dev" ref, only "1.x". Confirmed live:
    .../−/raw/1.x/composer.json → 200, .../−/raw/1.x-dev/composer.json → 404.
    Stable versions (e.g. "1.1.0") pass through unchanged since they ARE
    real tags.

    '1.x-dev' → '1.x'
    '1.1.0'   → '1.1.0'
    """
    return _DEV_SUFFIX_RE.sub('', version)


def get_recipe_label(machine_name: str, version: str) -> str | None:
    """Fetch a recipe's human-readable label from its recipe.yml file.

    Recipes don't have a {name}.info.yml like modules do — their display
    title lives in the `name:` key of a fixed-filename recipe.yml at the
    repo root instead. Reuses _INFO_NAME_RE since it's a generic top-level
    `name:` matcher, not module-specific.

    Returns None if the file can't be fetched or has no `name:` key.
    """
    ref = _git_ref_for_version(version)
    url = RECIPE_YML_URL.format(name=machine_name, ref=ref)
    try:
        status, body, _ = _http_get(url)
    except RuntimeError:
        return None
    if status != 200 or not body:
        return None

    match = _INFO_NAME_RE.search(body.decode("utf-8", errors="replace"))
    if not match:
        return None

    label = match.group(1).strip()
    if len(label) >= 2 and label[0] == label[-1] and label[0] in "'\"":
        label = label[1:-1]
    return label or None


def _fetch_recipe_label_with_delay(machine_name: str, version: str):
    """Sleep then fetch the recipe.yml label — designed to run in a worker thread."""
    time.sleep(GITLAB_DELAY)
    return machine_name, get_recipe_label(machine_name, version)


# ---------------------------------------------------------------------------
# Release date fallback — updates.drupal.org
# ---------------------------------------------------------------------------

def get_release_date_fallback(machine_name: str, version: str) -> str | None:
    """Look up a release date from updates.drupal.org.

    Only called when the p2 file has no datestamp. The updates.drupal.org
    endpoint returns an XML document listing all releases — the same feed
    Drupal core uses to display "update available" messages in the admin UI.
    """
    url = RELEASE_HISTORY_URL.format(name=machine_name)
    status, body, _ = _http_get(url)
    if status != 200 or not body:
        return None

    try:
        # ET.fromstring() parses an XML string into a tree of Element objects.
        # This is like PHP's new SimpleXMLElement($body) or
        # $doc = new DOMDocument(); $doc->loadXML($body);
        root = ET.fromstring(body)
    except ET.ParseError:
        return None

    first_published_date = None

    # root.findall(".//release") finds all <release> elements anywhere in the
    # XML tree. The ".//..." XPath syntax is like PHP's
    # $xpath->query('//release'). Each element is like a SimpleXMLElement node.
    for release in root.findall(".//release"):

        # release.findtext("status") gets the text content of the <status>
        # child element, like PHP's (string)$release->status
        if release.findtext("status") != "published":
            continue

        ts = release.findtext("date")  # Unix timestamp as a string
        if not ts:
            continue

        # Record the first published release date we encounter as a fallback
        # in case we never find the exact version match below.
        if first_published_date is None:
            first_published_date = datetime.fromtimestamp(
                int(ts), tz=timezone.utc
            ).strftime("%Y-%m-%d")

        # If this release matches the exact version from the p2 file, return
        # its date immediately (like PHP's `return` inside a foreach).
        if release.findtext("version") == version:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")

    # Return the most recent published date even if we didn't find an exact
    # version match — better than returning nothing.
    return first_published_date


# ---------------------------------------------------------------------------
# Usage count — Drupal.org API
# ---------------------------------------------------------------------------

def get_usage_and_security(machine_name: str) -> tuple[int | None, bool]:
    """Fetch the active install count and security advisory coverage.

    Both come from the same Drupal.org JSON API node, so they're fetched
    together to avoid a second API round-trip.

    The API returns a `project_usage` object like:
      {"1.0.x": 1500, "1.1.x": 3200}
    We sum all version counts to get a single total.

    `field_security_advisory_coverage` is `"covered"` for projects covered
    by Drupal's security advisory policy, `"not-covered"` otherwise. Any
    other/missing value is treated as not covered.

    Returns (usage_or_None, is_security_covered).
    """
    data = _drupal_api_get({
        "field_project_machine_name": machine_name,
        "limit": 1,  # we only need the one matching project node
    })
    if not data:
        return None, False

    # data.get("list", []) retrieves the array of node results.
    nodes = data.get("list", [])
    if not nodes:
        return None, False

    # nodes[0] is the first (and only) result — like PHP's $nodes[0].
    node = nodes[0]
    usage = node.get("project_usage") or {}

    # This is a generator expression inside sum() — a concise way to
    # convert all values to int and add them up. It's equivalent to:
    #   $total = 0;
    #   foreach ($usage as $v) { $total += (int)$v; }
    # The API returns counts as strings, so we cast each with int().
    total_usage = sum(int(v) for v in usage.values()) if usage else None

    is_covered = node.get("field_security_advisory_coverage") == "covered"

    return total_usage, is_covered


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def human_name(machine_name: str) -> str:
    """Convert a machine name to a readable title.

    'ai_image_alt_text' → 'Ai Image Alt Text'

    str.replace() is like PHP's str_replace().
    str.title() capitalises the first letter of each word,
    like PHP's ucwords().
    """
    return machine_name.replace("_", " ").title()


_STABILITY_RE = re.compile(r'-(?P<level>alpha|beta|rc|dev)', re.IGNORECASE)

def detect_stability(version: str) -> str:
    """Return the stability level of a version string.

    '1.2.3'        → 'stable'
    '1.0.0-alpha1' → 'alpha'
    '2.0.0-beta'   → 'beta'
    '1.5.0-rc1'    → 'rc'
    '1.0.0-dev'    → 'dev'
    ''             → 'stable'
    """
    if not version:
        return "stable"
    m = _STABILITY_RE.search(version)
    return m.group("level").lower() if m else "stable"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point — parse arguments, orchestrate all steps, output results."""

    # argparse handles command-line argument parsing.
    # It's like PHP's getopt() but with automatic --help generation.
    # __doc__ is the module's docstring (the triple-quoted string at the top
    # of the file) — Python stores it automatically as a special variable.
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--json", metavar="FILE",
        help="Write raw results to FILE as JSON. Omit to print JSON to stdout. "
             "Render with render_md.py / render_html.py — this script only collects data.",
    )
    # args.json will be a filename string if provided, or None if omitted (print to stdout).
    args = parser.parse_args()

    # -------------------------------------------------------------------------
    # Step 1: Collect candidate module names from both sources
    # -------------------------------------------------------------------------
    print("Collecting candidates …", file=sys.stderr)

    # `file=sys.stderr` sends output to STDERR instead of STDOUT.
    # This keeps progress messages separate from the markdown output,
    # so piping or redirecting stdout only captures the final table.
    print("  [1/2] Scraping AI ecosystem pages …", file=sys.stderr)
    eco_names = get_ecosystem_candidates()
    print(f"        {len(eco_names)} names found", file=sys.stderr)
    # len() is like PHP's count().

    print("  [2/2] Searching packages.drupal.org for 'ai' …", file=sys.stderr)
    search_names = get_search_candidates("ai")
    print(f"        {len(search_names)} names found", file=sys.stderr)

    # Union-merge: start with ecosystem names (preserving their order),
    # then append any search names not already in the set.
    # set(eco_names) builds a set from a list in one step — like array_flip()
    # in PHP to create an O(1) lookup table.
    seen: set[str] = set(eco_names)
    all_candidates = list(eco_names)  # list() copies the list
    for n in search_names:
        if n not in seen:
            seen.add(n)
            all_candidates.append(n)
    print(f"  {len(all_candidates)} unique candidates after merge\n", file=sys.stderr)

    # -------------------------------------------------------------------------
    # Step 2: Verify each candidate against packages.drupal.org
    # -------------------------------------------------------------------------
    # Phase 2a: concurrent p2 fetches (3 workers, each sleeping PACKAGES_DELAY
    # before its own request). packages.drupal.org serves static CDN files and
    # tolerates this rate; drupal.org's sensitive API is handled separately below.
    print("Verifying via packages.drupal.org p2 files …", file=sys.stderr)

    rows          = []   # will hold one dict per confirmed module
    skipped       = 0    # count of candidates that failed verification
    date_fallbacks = 0   # count of modules that needed the updates.drupal.org fallback
    total         = len(all_candidates)
    p2_map        = {}   # machine_name → p2 result dict (only passing modules)

    with ThreadPoolExecutor(max_workers=3) as pool:
        future_to_name = {pool.submit(_fetch_p2_with_delay, n): n for n in all_candidates}
        done = 0
        for fut in as_completed(future_to_name):
            done += 1
            name = future_to_name[fut]
            try:
                _, result = fut.result()
            except Exception as exc:
                print(f"  [{done}/{total}] {name}: error — {exc}", file=sys.stderr)
                result = None
            if result is not None:
                p2_map[name] = result
                print(f"  [{done}/{total}] {name}: ok", file=sys.stderr)
            else:
                skipped += 1
                print(f"  [{done}/{total}] {name}: skip", file=sys.stderr)

    # Phase 2b: concurrent info.yml label fetches for verified modules only
    # (3 workers, same pattern as phase 2a). git.drupalcode.org is a static
    # GitLab raw-file endpoint, so it tolerates the same burst rate as
    # packages.drupal.org.
    print("\nFetching module labels from info.yml files …", file=sys.stderr)
    verified = [(n, p2_map[n]) for n in all_candidates if n in p2_map]
    label_map = {}  # machine_name → label (only when found)

    with ThreadPoolExecutor(max_workers=3) as pool:
        future_to_name = {
            pool.submit(_fetch_label_with_delay, n, p2["version"]): n
            for n, p2 in verified
        }
        done = 0
        for fut in as_completed(future_to_name):
            done += 1
            name = future_to_name[fut]
            try:
                _, label = fut.result()
            except Exception as exc:
                print(f"  [{done}/{len(verified)}] {name}: error — {exc}", file=sys.stderr)
                label = None
            if label:
                label_map[name] = label
            print(f"  [{done}/{len(verified)}] {name}: {label or '(using fallback name)'}", file=sys.stderr)

    # Phase 2c: sequential usage + security coverage fetches for verified
    # modules only. Preserves all_candidates order (ecosystem-first) for
    # deterministic output. drupal.org's JSON API stays strictly sequential
    # at DRUPAL_API_DELAY.
    print("\nFetching usage counts …", file=sys.stderr)
    for i, (machine_name, p2) in enumerate(verified, 1):
        date = p2["date"]
        if not date:
            time.sleep(RELEASE_DELAY)
            date = get_release_date_fallback(machine_name, p2["version"])
            date_fallbacks += 1
            if date:
                print(f"  [{i}/{len(verified)}] {machine_name}: date from updates.drupal.org", file=sys.stderr)

        time.sleep(DRUPAL_API_DELAY)
        usage, security_covered = get_usage_and_security(machine_name)
        print(f"  [{i}/{len(verified)}] {machine_name}: usage={usage} security_covered={security_covered}", file=sys.stderr)

        rows.append({
            "machine_name":      p2["composer_name"] or f"drupal/{machine_name}",
            "label":             label_map.get(machine_name) or human_name(machine_name),
            "url":               DRUPAL_PROJECT_URL.format(name=machine_name),
            "version":           p2["version"],
            "release_date":      date or "—",
            "usage":             usage,
            "security_covered":  security_covered,
            "stability":         detect_stability(p2["version"]),
        })

    print(
        f"\nResults: {len(rows)} confirmed modules "
        f"({skipped} skipped, {date_fallbacks} used date fallback)",
        file=sys.stderr,
    )

    # -------------------------------------------------------------------------
    # Step 1b: Collect candidate recipe names (3 sources, merged)
    # -------------------------------------------------------------------------
    # Recipes are verified separately from modules (different registry, no
    # usage/security data), so they get their own candidate-discovery step.
    print("\nCollecting recipe candidates …", file=sys.stderr)

    print("  [1/3] Reusing AI ecosystem names not matched as modules …", file=sys.stderr)
    eco_unmatched = [n for n in eco_names if n not in p2_map]
    print(f"        {len(eco_unmatched)} names", file=sys.stderr)

    print("  [2/3] Searching Packagist for type=drupal-recipe, q=ai …", file=sys.stderr)
    recipe_search_names = get_recipe_search_candidates()
    print(f"        {len(recipe_search_names)} names found", file=sys.stderr)

    print("  [3/3] Fetching curated AI recipe list …", file=sys.stderr)
    curated_recipe_names = get_curated_recipe_candidates()
    print(f"        {len(curated_recipe_names)} names found", file=sys.stderr)

    seen_recipes: set[str] = set()
    all_recipe_candidates = []
    for n in eco_unmatched + recipe_search_names + curated_recipe_names:
        if n not in seen_recipes:
            seen_recipes.add(n)
            all_recipe_candidates.append(n)
    print(f"  {len(all_recipe_candidates)} unique recipe candidates after merge\n", file=sys.stderr)

    # -------------------------------------------------------------------------
    # Step 2d: Verify each recipe candidate against repo.packagist.org
    # -------------------------------------------------------------------------
    print("Verifying recipes via repo.packagist.org p2 files …", file=sys.stderr)

    recipe_rows    = []
    recipe_skipped = 0
    recipe_total   = len(all_recipe_candidates)
    recipe_p2_map  = {}

    with ThreadPoolExecutor(max_workers=3) as pool:
        future_to_name = {
            pool.submit(_fetch_recipe_info_with_delay, n): n for n in all_recipe_candidates
        }
        done = 0
        for fut in as_completed(future_to_name):
            done += 1
            name = future_to_name[fut]
            try:
                _, result = fut.result()
            except Exception as exc:
                print(f"  [{done}/{recipe_total}] {name}: error — {exc}", file=sys.stderr)
                result = None
            if result is not None:
                recipe_p2_map[name] = result
                print(f"  [{done}/{recipe_total}] {name}: ok", file=sys.stderr)
            else:
                recipe_skipped += 1
                print(f"  [{done}/{recipe_total}] {name}: skip", file=sys.stderr)

    # Phase 2e: concurrent recipe.yml label fetches for verified recipes only.
    print("\nFetching recipe labels from recipe.yml files …", file=sys.stderr)
    verified_recipes = [(n, recipe_p2_map[n]) for n in all_recipe_candidates if n in recipe_p2_map]
    recipe_label_map = {}

    with ThreadPoolExecutor(max_workers=3) as pool:
        future_to_name = {
            pool.submit(_fetch_recipe_label_with_delay, n, info["version"]): n
            for n, info in verified_recipes
        }
        done = 0
        for fut in as_completed(future_to_name):
            done += 1
            name = future_to_name[fut]
            try:
                _, label = fut.result()
            except Exception as exc:
                print(f"  [{done}/{len(verified_recipes)}] {name}: error — {exc}", file=sys.stderr)
                label = None
            if label:
                recipe_label_map[name] = label
            print(f"  [{done}/{len(verified_recipes)}] {name}: {label or '(using fallback name)'}", file=sys.stderr)

    for machine_name, info in verified_recipes:
        recipe_rows.append({
            "machine_name": info["composer_name"] or f"drupal/{machine_name}",
            "label":        recipe_label_map.get(machine_name) or human_name(machine_name),
            "url":          DRUPAL_PROJECT_URL.format(name=machine_name),
            "version":      info["version"],
            "release_date": info["date"] or "—",
            "stability":    detect_stability(info["version"]),
        })

    # No usage signal exists for recipes, so sort alphabetically by label
    # instead of by usage (modules' sort key below).
    recipe_rows.sort(key=lambda r: r["label"].lower())

    print(
        f"\nResults: {len(recipe_rows)} confirmed recipes ({recipe_skipped} skipped)",
        file=sys.stderr,
    )

    # -------------------------------------------------------------------------
    # Step 3: Sort results by usage count, highest first
    # -------------------------------------------------------------------------

    # list.sort() sorts in place (modifies the list). The `key` argument
    # accepts a function that extracts the sort value from each element —
    # like PHP's usort() with a comparison callback.
    # `lambda r: ...` is an anonymous function — like PHP's fn($r) => ...
    # Modules with no usage data (None) sort to the bottom via the -1 fallback.
    rows.sort(key=lambda r: r["usage"] if r["usage"] else -1, reverse=True)

    # -------------------------------------------------------------------------
    # Step 4: Write or print JSON
    # -------------------------------------------------------------------------
    # This script only collects and verifies data — rendering is render_md.py's
    # and render_html.py's job. Keeping JSON as the single output format means
    # those two renderers (which get updated when columns/filters change) are
    # never at risk of drifting out of sync with a third, inline renderer here.

    payload = {
        "generated":       datetime.now().strftime("%Y-%m-%d"),
        "drupal_versions": sorted(TARGET_VERSIONS),
        "modules":         rows,
        "recipes":         recipe_rows,
    }

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        print(f"JSON written to {args.json}", file=sys.stderr)
    else:
        # print() with no file argument writes to STDOUT, so it can be
        # cleanly piped or redirected: python3 script.py > results.json
        print(json.dumps(payload, indent=2, ensure_ascii=False))


# This block only runs when the script is executed directly:
#   python3 drupal_ai_dependents.py
# It does NOT run when another Python file imports this one.
# The PHP equivalent would be checking if the file is the entry point
# (there's no direct PHP equivalent, but it's similar to Symfony's
# public/index.php being the only file that should bootstrap the kernel).
if __name__ == "__main__":
    main()
