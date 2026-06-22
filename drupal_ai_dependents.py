#!/usr/bin/env python3
"""
Find all Drupal modules with a hard dependency on drupal/ai
(https://www.drupal.org/project/ai) and output a markdown table showing:
module name, URL, latest version, release date, and active install count.

Candidate discovery (two sources, union-merged):
  1. drupal.org/project/ai/ecosystem  — curated AI ecosystem listing
  2. packages.drupal.org search "ai"  — package name/description search

Dependency verification (authoritative, reads composer.json directly):
  packages.drupal.org/files/packages/8/p2/drupal/{name}.json
  Confirms type=drupal-module AND drupal/ai is in the require field.
  Also supplies version, Drupal core constraint, and release datestamp.

Additional data:
  updates.drupal.org           — release date fallback (when p2 has no datestamp)
  www.drupal.org/api-d7/node.json — usage/install count

Usage:
  python3 drupal_ai_dependents.py [--json FILE] [--output FILE] [--html FILE]
"""

# Python's standard library modules — no composer/npm needed.
# Think of these like PHP's built-in extensions (json_decode, preg_match, etc.)
import argparse           # parses command-line flags like --output
import html               # html.escape() for safe HTML attribute/text escaping
import json               # like PHP's json_decode() / json_encode()
import re                 # like PHP's preg_match() / preg_replace()
import sys                # access to stdin/stdout/stderr and script exit
import time               # like PHP's sleep() and microtime()
import urllib.error       # HTTP error types thrown by urllib (like curl errors)
import urllib.parse       # URL building — like PHP's http_build_query()
import urllib.request     # makes HTTP requests — like PHP's curl or file_get_contents()
import xml.etree.ElementTree as ET  # XML parser — like PHP's SimpleXMLElement
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
    if not versions:
        return None

    # Check 1: is this actually a Drupal module (not a recipe, profile, etc.)?
    # The `type` field in composer.json is stable across versions, so checking
    # only the first (latest) entry is sufficient.
    if versions[0].get("type") != "drupal-module":
        return None

    # Iterate through versions newest-first to find the latest one that
    # satisfies all our requirements.
    for v in versions:
        # `v.get("require") or {}` safely gets the require dict.
        # The `or {}` fallback handles the case where require is null/missing,
        # avoiding a TypeError on the `in` check below.
        # This is like PHP's $require = $v['require'] ?? [];
        require = v.get("require") or {}

        # Check 2: does this version declare drupal/ai as a dependency?
        if "drupal/ai" not in require:
            continue  # skip to next version, like PHP's `continue`

        # Check 3: does the drupal/core constraint include Drupal 10 or 11?
        core_constraint = require.get("drupal/core", "")
        if not _core_is_d10_d11(core_constraint):
            continue

        # Extract the release date from the Drupal-specific extra metadata.
        # In composer.json this looks like: "extra": {"drupal": {"datestamp": 1772029063}}
        # Chaining .get() calls safely navigates nested dicts without KeyErrors —
        # like PHP's $v['extra']['drupal']['datestamp'] ?? null but without
        # throwing notices on missing intermediate keys.
        datestamp = (v.get("extra") or {}).get("drupal", {}).get("datestamp")

        # Convert the Unix timestamp to a YYYY-MM-DD string if we have one.
        # This is a conditional expression (ternary): value_if_true if condition else value_if_false
        # timezone.utc ensures we interpret the timestamp as UTC, like
        # PHP's (new DateTime())->setTimestamp($ts)->format('Y-m-d')
        date = (
            datetime.fromtimestamp(int(datestamp), tz=timezone.utc).strftime("%Y-%m-%d")
            if datestamp
            else None
        )

        # Return a dict (associative array) with everything callers need.
        # Once we find the first valid version, we stop — no need to check older ones.
        return {
            "version":         v.get("version", ""),
            "date":            date,
            "ai_constraint":   require["drupal/ai"],    # e.g. "^1.2.0"
            "core_constraint": core_constraint,         # e.g. "^10.3 || ^11"
        }

    # If we checked every version and none passed, return None.
    return None


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

def get_usage_count(machine_name: str) -> int | None:
    """Fetch the total active install count from the Drupal.org JSON API.

    The API returns a `project_usage` object like:
      {"1.0.x": 1500, "1.1.x": 3200}
    We sum all version counts to get a single total.
    Returns None if the API call fails or no usage data exists.
    """
    data = _drupal_api_get({
        "field_project_machine_name": machine_name,
        "limit": 1,  # we only need the one matching project node
    })
    if not data:
        return None

    # data.get("list", []) retrieves the array of node results.
    nodes = data.get("list", [])
    if not nodes:
        return None

    # nodes[0] is the first (and only) result — like PHP's $nodes[0].
    usage = nodes[0].get("project_usage") or {}

    # This is a generator expression inside sum() — a concise way to
    # convert all values to int and add them up. It's equivalent to:
    #   $total = 0;
    #   foreach ($usage as $v) { $total += (int)$v; }
    # The API returns counts as strings, so we cast each with int().
    return sum(int(v) for v in usage.values()) if usage else None


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
# HTML output
# ---------------------------------------------------------------------------

def render_html(rows: list[dict]) -> str:
    """Return a self-contained HTML file with a sortable, filterable results table."""
    today   = datetime.now().strftime("%Y-%m-%d")
    v_label = "/".join(str(v) for v in sorted(TARGET_VERSIONS))
    count   = len(rows)

    def esc(s: str) -> str:
        return html.escape(str(s), quote=True)

    row_lines = []
    for r in rows:
        name_esc   = esc(r["name"])
        url_esc    = esc(r["url"])
        ver_raw    = r["version"]
        ver_disp   = esc(ver_raw) if ver_raw else "—"
        ver_val    = esc(ver_raw) if ver_raw else ""
        date       = r["release_date"]
        date_val   = "" if date == "—" else esc(date)
        usage_raw  = str(r["usage"]) if r["usage"] else ""
        usage_disp = f"{r['usage']:,}" if r["usage"] else "—"
        row_lines.append(
            f'      <tr>'
            f'<td data-val="{name_esc}"><a href="{url_esc}">{name_esc}</a></td>'
            f'<td data-val="{ver_val}" class="col-version">{ver_disp}</td>'
            f'<td data-val="{date_val}" class="col-date">{esc(date)}</td>'
            f'<td data-val="{usage_raw}" class="col-usage">{usage_disp}</td>'
            f'</tr>'
        )

    tbody = "\n".join(row_lines)

    css = """\
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
    #filter {
      display: block;
      margin-bottom: 1rem;
      padding: 0.45rem 0.75rem;
      font-size: 1rem;
      width: 300px;
      border: 1px solid #bbb;
      border-radius: 4px;
      outline-color: #2d6a9f;
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
    .col-version { font-family: ui-monospace, monospace; white-space: nowrap; }
    .col-date    { white-space: nowrap; }
    .col-usage   { text-align: right; }
    th.col-usage { text-align: right; }
    #no-results {
      display: none;
      padding: 1.5rem;
      text-align: center;
      color: #888;
      background: #fff;
      border-top: 1px solid #eee;
    }"""

    js = """\
    (function () {
      var thead = document.querySelector('#tbl thead tr');
      var tbody = document.getElementById('tbody');
      var filter = document.getElementById('filter');
      var noResults = document.getElementById('no-results');
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

      sortTable(3, 'num');

      filter.addEventListener('input', function () {
        var q = filter.value.toLowerCase();
        var visible = 0;
        Array.from(tbody.querySelectorAll('tr')).forEach(function (row) {
          var show = cellVal(row, 0).toLowerCase().indexOf(q) !== -1;
          row.style.display = show ? '' : 'none';
          if (show) visible++;
        });
        noResults.style.display = (visible === 0) ? '' : 'none';
      });
    })();"""

    return (
        '<!DOCTYPE html>\n'
        '<html lang="en">\n'
        '<head>\n'
        '  <meta charset="UTF-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        '  <title>Drupal AI Dependents</title>\n'
        f'  <style>\n{css}\n  </style>\n'
        '</head>\n'
        '<body>\n'
        '  <h1>Drupal Modules &mdash; '
        '<a href="https://www.drupal.org/project/ai">drupal/ai</a> Dependents</h1>\n'
        f'  <p class="meta">Generated {today} &middot; {count} modules'
        f' &middot; Drupal {v_label} compatible &middot; all stability levels</p>\n'
        '  <input id="filter" type="search" placeholder="Filter by module name…">\n'
        '  <table id="tbl">\n'
        '    <thead>\n'
        '      <tr>\n'
        '        <th data-col="0" data-type="text">Module</th>\n'
        '        <th data-col="1" data-type="text">Version</th>\n'
        '        <th data-col="2" data-type="date">Released</th>\n'
        '        <th data-col="3" data-type="num" class="col-usage">Installs</th>\n'
        '      </tr>\n'
        '    </thead>\n'
        '    <tbody id="tbody">\n'
        f'{tbody}\n'
        '    </tbody>\n'
        '  </table>\n'
        '  <p id="no-results">No modules match your filter.</p>\n'
        f'  <script>\n{js}\n  </script>\n'
        '</body>\n'
        '</html>\n'
    )


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
        "--output", "-o", metavar="FILE",
        help="Write markdown to FILE instead of stdout",
    )
    parser.add_argument(
        "--html", metavar="FILE",
        help="Write a self-contained HTML table to FILE (sortable by any column, filterable by name)",
    )
    parser.add_argument(
        "--json", metavar="FILE",
        help="Write raw results to FILE as JSON (re-render later with render_md.py / render_html.py)",
    )
    # args.output / args.html / args.json will be filename strings if provided, or None if omitted.
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
    print("Verifying via packages.drupal.org p2 files …", file=sys.stderr)

    rows          = []   # will hold one dict per confirmed module
    skipped       = 0    # count of candidates that failed verification
    date_fallbacks = 0   # count of modules that needed the updates.drupal.org fallback
    total         = len(all_candidates)

    # enumerate() adds a counter to a loop — like PHP's
    # foreach ($all_candidates as $i => $machine_name)
    # The second argument `1` makes the counter start at 1 instead of 0.
    for i, machine_name in enumerate(all_candidates, 1):
        print(f"  [{i}/{total}] {machine_name} …", file=sys.stderr)

        # Sleep BEFORE the request (not after) so there's always a gap
        # between consecutive calls to the same server.
        time.sleep(PACKAGES_DELAY)
        p2 = get_p2_info(machine_name)

        # `is None` is the correct way to check for null in Python —
        # equivalent to PHP's === null. Using `== None` would also match
        # other falsy values and is considered bad practice.
        if p2 is None:
            print(f"    → skipped", file=sys.stderr)
            skipped += 1
            continue  # move to the next iteration of the for loop

        # Use the date from the p2 file if available; otherwise fall back
        # to querying updates.drupal.org.
        date = p2["date"]
        if not date:
            time.sleep(RELEASE_DELAY)
            date = get_release_date_fallback(machine_name, p2["version"])
            date_fallbacks += 1
            if date:
                print(f"    date from updates.drupal.org", file=sys.stderr)

        time.sleep(DRUPAL_API_DELAY)
        usage = get_usage_count(machine_name)

        # Append a dict to our results list. In PHP this would be:
        # $rows[] = ["name" => ..., "url" => ..., ...]
        rows.append({
            "machine_name": machine_name,
            "name":         human_name(machine_name),
            "url":          DRUPAL_PROJECT_URL.format(name=machine_name),
            "version":      p2["version"],
            "release_date": date or "—",  # `or` here means "if falsy, use this instead"
            "usage":        usage,
            "stability":    detect_stability(p2["version"]),
        })

    print(
        f"\nResults: {len(rows)} confirmed modules "
        f"({skipped} skipped, {date_fallbacks} used date fallback)",
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
    # Step 4: Write JSON if requested
    # -------------------------------------------------------------------------

    if args.json:
        payload = {
            "generated":      datetime.now().strftime("%Y-%m-%d"),
            "drupal_versions": sorted(TARGET_VERSIONS),
            "modules":        rows,
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        print(f"JSON written to {args.json}", file=sys.stderr)

    # -------------------------------------------------------------------------
    # Step 6: Render the markdown table
    # -------------------------------------------------------------------------

    today   = datetime.now().strftime("%Y-%m-%d")  # like PHP's date('Y-m-d')

    # " ".join(iterable) concatenates a list with a separator —
    # like PHP's implode("/", [...])
    # `str(v) for v in sorted(TARGET_VERSIONS)` is a generator expression:
    # it converts each number in the sorted set to a string, like
    # PHP's array_map('strval', $sorted_versions)
    v_label = "/".join(str(v) for v in sorted(TARGET_VERSIONS))

    # Build the markdown output as a list of strings, then join them.
    # f-strings use {variable} interpolation — like PHP's "..." with double
    # quotes, or more precisely like "Value is {$var}" in a heredoc.
    lines = [
        f"# Drupal Modules with a Hard Dependency on [AI](https://www.drupal.org/project/ai)\n",
        f"*Generated {today} · {len(rows)} modules · Drupal {v_label} compatible · all stability levels*\n",
        "| Module | URL | Latest Version | Release Date | Usage (installs) |",
        "|--------|-----|:--------------:|:------------:|----------------:|",
    ]

    for r in rows:
        # f"{r['usage']:,}" formats a number with thousands separators:
        # 10508 → "10,508". The `:,` is a format spec inside an f-string,
        # similar to PHP's number_format($n, 0, '.', ',')
        usage_str = f"{r['usage']:,}" if r["usage"] else "—"
        lines.append(
            f"| {r['name']} | {r['url']} | `{r['version']}` | {r['release_date']} | {usage_str} |"
        )

    # "\n".join(lines) is exactly PHP's implode("\n", $lines)
    output = "\n".join(lines) + "\n"

    # -------------------------------------------------------------------------
    # Step 7: Write to file or print to stdout
    # -------------------------------------------------------------------------

    if args.output:
        # `with open(...) as fh:` is a context manager that ensures the file
        # is closed when the block exits — like PHP's fopen/fclose wrapped in
        # a try/finally. "w" = write mode, like PHP's fopen($path, 'w').
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(output)
        print(f"Wrote {len(rows)} rows to {args.output}", file=sys.stderr)
    else:
        # print() with no file argument writes to STDOUT —
        # the markdown table is the only thing on stdout, so it can be
        # cleanly piped or redirected: python3 script.py > results.md
        print(output)

    if args.html:
        with open(args.html, "w", encoding="utf-8") as fh:
            fh.write(render_html(rows))
        print(f"HTML written to {args.html}", file=sys.stderr)


# This block only runs when the script is executed directly:
#   python3 drupal_ai_dependents.py
# It does NOT run when another Python file imports this one.
# The PHP equivalent would be checking if the file is the entry point
# (there's no direct PHP equivalent, but it's similar to Symfony's
# public/index.php being the only file that should bootstrap the kernel).
if __name__ == "__main__":
    main()
