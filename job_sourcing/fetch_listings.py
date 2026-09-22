#!/usr/bin/env python3
"""Fetch real Singapore job listings from the public MyCareersFuture API.

MyCareersFuture is Singapore's government job bank. Under the Fair Consideration
Framework most SG roles above the salary threshold must be posted there for at
least 14 days before an Employment Pass application, which makes it the single
most complete public index of the local market. Its API is public and needs no
key or account.

Design constraints:

1. The exact endpoint shape is not officially documented and public references
   disagree (GET /v2/jobs vs POST /v2/search, two hostnames). This script probes
   a ranked list of candidates, records which one answered, and reuses it.
   CONFIRMED 2026-09-12: POST https://api.mycareersfuture.gov.sg/v2/search
   answers, returning {results:[...]}. The search response carries title,
   company, skills, categories, salary and dates but NO description - that
   only comes from the per-job detail endpoint, so listings are hydrated
   separately below.
2. It NEVER writes placeholder, sample or synthesised listings. If no candidate
   endpoint answers, it exits non-zero and leaves the previous data untouched.
   A downstream resume tailored against an invented job posting is worse than
   no data at all.

Stdlib only, so GitHub Actions needs no dependency install.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
QUERIES_FILE = HERE / "queries.json"
LISTINGS_FILE = DATA / "listings.json"
META_FILE = DATA / "_meta.json"

UA = "studious-memory-job-sourcing/1.0 (+https://github.com/alexchoh/studious-memory)"
PAGE_SIZE = 100
TIMEOUT = 45


# --------------------------------------------------------------------------
# endpoint probing
# --------------------------------------------------------------------------

def _candidates(query: str, page: int, limit: int):
    """Ranked candidate request shapes, most likely first."""
    qs = urllib.parse.urlencode({"limit": limit, "page": page})
    body = {
        "search": query,
        "sessionId": "",
        "categories": [],
        "employmentTypes": [],
        "positionLevels": [],
        "sortBy": ["new_posting_date"],
    }
    getqs = urllib.parse.urlencode(
        {"search": query, "limit": limit, "page": page, "sortBy": "new_posting_date"}
    )
    return [
        ("post_v2_search_gov",
         f"https://api.mycareersfuture.gov.sg/v2/search?{qs}", "POST", body),
        ("get_v2_jobs_gov",
         f"https://api.mycareersfuture.gov.sg/v2/jobs?{getqs}", "GET", None),
        ("post_v2_search_sg",
         f"https://api.mycareersfuture.sg/v2/search?{qs}", "POST", body),
        ("get_v2_jobs_sg",
         f"https://api1.mycareersfuture.sg/v2/jobs?{getqs}", "GET", None),
    ]


def _request(url: str, method: str, body):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json", "User-Agent": UA}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw)


def _extract_results(payload):
    """MCF has used both {results:[...]} and a bare list. Accept either."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "jobs", "data", "content"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
    return None


def probe(query: str):
    """Return (shape_name, url_template_fn) for the first candidate that answers."""
    errors = []
    for name, url, method, body in _candidates(query, 0, 5):
        try:
            payload = _request(url, method, body)
        except Exception as exc:  # noqa: BLE001 - probing is meant to be broad
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        results = _extract_results(payload)
        if results is None:
            errors.append(f"{name}: 200 but no recognisable results array "
                          f"(top-level keys: {list(payload)[:8] if isinstance(payload, dict) else type(payload).__name__})")
            continue
        print(f"  probe OK -> {name} ({len(results)} records in sample)", file=sys.stderr)
        return name, errors
    return None, errors


def fetch_shape(shape: str, query: str, page: int, limit: int):
    for name, url, method, body in _candidates(query, page, limit):
        if name == shape:
            return _request(url, method, body)
    raise RuntimeError(f"unknown shape {shape}")


def _detail_candidates(uuid: str):
    return [
        ("get_v2_jobs_uuid", f"https://api.mycareersfuture.gov.sg/v2/jobs/{uuid}"),
        ("get_v2_job_uuid",  f"https://api.mycareersfuture.gov.sg/v2/job/{uuid}"),
    ]


def probe_detail(uuid: str):
    """Find the per-job detail endpoint. Returns (shape_name, errors)."""
    errors = []
    for name, url in _detail_candidates(uuid):
        try:
            payload = _request(url, "GET", None)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        if isinstance(payload, dict) and (payload.get("description") or payload.get("uuid")):
            print(f"  detail probe OK -> {name}", file=sys.stderr)
            return name, errors
        errors.append(f"{name}: 200 but no description/uuid in payload")
    return None, errors


def hydrate(items, shape, limit):
    """Fetch full descriptions for the first `limit` listings.

    The search endpoint returns summaries only. Fit scoring and drafting need
    the description, so the ones most likely to be read are filled in. A
    listing that cannot be hydrated keeps its empty description rather than
    being given invented text.
    """
    done = 0
    for item in items[:limit]:
        uuid = item["id"].split(":", 1)[-1]
        url = dict(_detail_candidates(uuid))[shape]
        try:
            rec = _request(url, "GET", None)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(rec, dict):
            continue
        item["description"] = to_text(rec.get("description"))
        item["requirements"] = to_text(rec.get("otherRequirements"))
        if rec.get("minimumYearsExperience") is not None:
            item["minYearsExperience"] = rec.get("minimumYearsExperience")
        for key, path in (("employmentTypes", ("employmentTypes",)),
                          ("positionLevels", ("positionLevels",))):
            vals = names(dig(rec, *path, default=[]))
            if vals:
                item[key] = vals
        sal_min, sal_max = dig(rec, "salary", "minimum"), dig(rec, "salary", "maximum")
        if sal_min:
            item["salaryMin"], item["salaryMax"] = sal_min, sal_max
        districts = names(dig(rec, "address", "districts", default=[]))
        if districts:
            item["districts"] = districts
        item["hydrated"] = True
        done += 1
        time.sleep(0.25)
    return done


# --------------------------------------------------------------------------
# boolean filtering
#
# MyCareersFuture has no boolean search syntax - it takes a plain phrase. So
# the boolean is applied HERE, over a deliberately broad fetch, which is the
# only way to get real AND/OR/NOT semantics out of this source.
# --------------------------------------------------------------------------

def _haystack(item) -> str:
    return norm_text(" ".join(filter(None, [
        item.get("title"), item.get("company"), item.get("description"),
        item.get("requirements"), " ".join(item.get("skills") or []),
        " ".join(item.get("categories") or []),
    ])))


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).lower()


def matches(item, cfg) -> bool:
    """Apply mustAll / mustAny / exclude from queries.json to one listing."""
    hay = _haystack(item)
    for term in cfg.get("exclude") or []:
        if norm_text(term) in hay:
            return False
    must_all = cfg.get("mustAll") or []
    if must_all and not all(norm_text(t) in hay for t in must_all):
        return False
    must_any = cfg.get("mustAny") or []
    if must_any and not any(norm_text(t) in hay for t in must_any):
        return False
    return True


# --------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t\r\f\v]+")
NL_RE = re.compile(r"\n{3,}")


def to_text(raw_html: str | None) -> str:
    if not raw_html:
        return ""
    text = re.sub(r"<\s*(br|/p|/li|/div|/h[1-6])\s*/?>", "\n", raw_html, flags=re.I)
    text = re.sub(r"<\s*li[^>]*>", "\n- ", text, flags=re.I)
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    text = WS_RE.sub(" ", text)
    text = NL_RE.sub("\n\n", text)
    return text.strip()


def dig(obj, *path, default=None):
    cur = obj
    for key in path:
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif isinstance(cur, list) and isinstance(key, int) and len(cur) > key:
            cur = cur[key]
        else:
            return default
        if cur is None:
            return default
    return cur


# MyCareersFuture keys each lookup list by its own singular field name rather
# than a shared "name". Confirmed against a real payload on 2026-09-22:
#   employmentTypes -> employmentType, positionLevels -> position,
#   categories -> category, skills -> skill,
#   flexibleWorkArrangements -> flexibleWorkArrangement
NAME_KEYS = ("name", "category", "skill", "employmentType", "position",
             "flexibleWorkArrangement", "scheme", "district", "region")


def names(seq):
    out = []
    for item in seq or []:
        if isinstance(item, dict):
            val = next((item[k] for k in NAME_KEYS if item.get(k)), None)
            if val:
                out.append(str(val))
        elif item:
            out.append(str(item))
    return out


def normalise(rec: dict, query: str) -> dict | None:
    uuid = rec.get("uuid") or dig(rec, "metadata", "jobPostId") or rec.get("jobPostId")
    title = (rec.get("title") or "").strip()
    if not title:
        return None
    company = (dig(rec, "postedCompany", "name")
               or dig(rec, "hiringCompany", "name")
               or rec.get("companyName") or "").strip()
    slug = re.sub(r"[^a-z0-9]+", "-", f"{company} {title}".lower()).strip("-")[:80]
    url = f"https://www.mycareersfuture.gov.sg/job/{slug}-{uuid}" if uuid else ""

    districts = names(dig(rec, "address", "districts", default=[]))
    if not districts:
        area = dig(rec, "address", "building") or dig(rec, "address", "street")
        if area:
            districts = [str(area).title()]

    return {
        "id": f"mcf:{uuid}",
        "source": "mycareersfuture",
        "title": title,
        "company": company,
        "uen": dig(rec, "postedCompany", "uen") or "",
        "url": url,
        "description": to_text(rec.get("description")),
        "requirements": to_text(rec.get("otherRequirements")),
        "skills": names(rec.get("skills")),
        "categories": names(rec.get("categories")),
        "employmentTypes": names(rec.get("employmentTypes")),
        "positionLevels": names(rec.get("positionLevels")),
        "minYearsExperience": rec.get("minimumYearsExperience"),
        # SG listings quote GROSS MONTHLY SGD, not annual. Keep it that way.
        "salaryMin": dig(rec, "salary", "minimum"),
        "salaryMax": dig(rec, "salary", "maximum"),
        "salaryType": dig(rec, "salary", "type", "salaryType") or dig(rec, "salary", "type", "id"),
        "districts": [d for d in districts if d],
        "workArrangements": names(rec.get("flexibleWorkArrangements")),
        "postedDate": dig(rec, "metadata", "newPostingDate") or dig(rec, "metadata", "originalPostingDate"),
        "expiryDate": dig(rec, "metadata", "expiryDate"),
        "applications": dig(rec, "metadata", "totalNumberJobApplication"),
        "views": dig(rec, "metadata", "totalNumberOfView"),
        "matchedQuery": query,
    }


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def load_queries() -> dict:
    if not QUERIES_FILE.exists():
        print(f"error: {QUERIES_FILE} not found. Export one from the "
              f"Sourcing stage of the Job Sourcing page.", file=sys.stderr)
        sys.exit(2)
    cfg = json.loads(QUERIES_FILE.read_text())
    if not cfg.get("queries"):
        print("error: queries.json has no 'queries' array.", file=sys.stderr)
        sys.exit(2)
    return cfg


def main() -> int:
    cfg = load_queries()
    queries = [q for q in cfg["queries"] if str(q).strip()]
    max_pages = int(cfg.get("maxPagesPerQuery", 3))
    posted_within_days = cfg.get("postedWithinDays")

    print(f"probing MyCareersFuture with {queries[0]!r} ...", file=sys.stderr)
    shape, probe_errors = probe(queries[0])
    if shape is None:
        print("error: no MyCareersFuture endpoint answered. Nothing written.",
              file=sys.stderr)
        for line in probe_errors:
            print(f"  - {line}", file=sys.stderr)
        return 1

    seen: dict[str, dict] = {}
    unknown_keys: set[str] = set()
    per_query: dict[str, int] = {}
    raw_sample: dict | None = None

    for query in queries:
        got = 0
        for page in range(max_pages):
            try:
                payload = fetch_shape(shape, query, page, PAGE_SIZE)
            except Exception as exc:  # noqa: BLE001
                print(f"  {query!r} page {page}: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                break
            results = _extract_results(payload) or []
            if not results:
                break
            for rec in results:
                if isinstance(rec, dict):
                    unknown_keys.update(rec.keys())
                    if raw_sample is None:
                        raw_sample = rec
                item = normalise(rec, query) if isinstance(rec, dict) else None
                if item and item["id"] not in seen:
                    seen[item["id"]] = item
                    got += 1
            if len(results) < PAGE_SIZE:
                break
            time.sleep(0.6)
        per_query[query] = got
        print(f"  {query!r}: {got} new", file=sys.stderr)

    listings = list(seen.values())
    if not listings:
        print("error: endpoint answered but returned zero listings. Nothing written.",
              file=sys.stderr)
        return 1

    if posted_within_days:
        cutoff = time.time() - int(posted_within_days) * 86400
        def recent(item):
            posted = item.get("postedDate")
            if not posted:
                return True
            try:
                return datetime.fromisoformat(str(posted)).timestamp() >= cutoff
            except ValueError:
                return True
        listings = [i for i in listings if recent(i)]

    before_filter = len(listings)
    listings = [i for i in listings if matches(i, cfg)]
    if not listings:
        print(f"error: all {before_filter} listings were removed by the mustAll / "
              f"mustAny / exclude rules in queries.json. Nothing written - "
              f"loosen the rules rather than shipping an empty feed.", file=sys.stderr)
        return 1
    print(f"  boolean filter: {before_filter} -> {len(listings)}", file=sys.stderr)

    listings.sort(key=lambda i: (i.get("postedDate") or ""), reverse=True)

    hydrated = 0
    detail_shape, detail_errors = probe_detail(listings[0]["id"].split(":", 1)[-1])
    if detail_shape:
        limit = int(cfg.get("hydrateTop", 80))
        print(f"  hydrating descriptions for top {limit} ...", file=sys.stderr)
        hydrated = hydrate(listings, detail_shape, limit)
        print(f"  hydrated {hydrated}", file=sys.stderr)
    else:
        print("  no detail endpoint answered; descriptions stay empty",
              file=sys.stderr)

    DATA.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    LISTINGS_FILE.write_text(json.dumps({
        "generatedAt": now,
        "source": "mycareersfuture.gov.sg public API",
        "endpointShape": shape,
        "count": len(listings),
        "hydrated": hydrated,
        "queries": queries,
        "filter": {k: cfg.get(k) for k in ("mustAll", "mustAny", "exclude") if cfg.get(k)},
        "listings": listings,
    }, indent=1, ensure_ascii=False))
    META_FILE.write_text(json.dumps({
        "generatedAt": now,
        "endpointShape": shape,
        "probeErrors": probe_errors,
        "perQueryNew": per_query,
        "observedRecordKeys": sorted(unknown_keys),
        "count": len(listings),
        "countBeforeFilter": before_filter,
        "hydrated": hydrated,
        "detailShape": detail_shape,
        "detailProbeErrors": detail_errors,
        # Kept so field mappings (salary, position levels) can be corrected
        # against a real payload instead of guessed at.
        "sampleRawRecord": raw_sample,
    }, indent=1, ensure_ascii=False))

    print(f"wrote {len(listings)} listings to {LISTINGS_FILE}", file=sys.stderr)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(f"### MyCareersFuture sync\n\n"
                     f"- Endpoint shape: `{shape}`\n"
                     f"- Listings: **{len(listings)}**\n"
                     f"- Queries: {', '.join(f'`{q}`' for q in queries)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
