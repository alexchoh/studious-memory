# Job sourcing and resume provenance

Two halves that work together:

| Part | What it is | Where it runs |
|---|---|---|
| `index.html` | The **Resume Provenance Desk** page — resume analysis, query building, fit scoring, and cited drafting | Published as a Claude Artifact |
| `fetch_listings.py` + workflow | A daily pull of **real** job listings from the MyCareersFuture public API | GitHub Actions |

## Searching Indeed from the page

The page now searches Indeed directly, through the viewer's own claude.ai
connector. This is the single outbound channel an artifact has: it cannot
`fetch`, but the `mcp` capability lets it call a connector the viewer installed,
and that call goes through the Claude host rather than the page's network.

Requires the **Indeed** connector connected in claude.ai. Without it the page
says so and the button is not shown.

Both tool shapes were observed from real calls before any code was written:

| Tool | Input | Returns |
|---|---|---|
| `search_jobs` | `search`, `location`, `country_code`, `job_type?` | one markdown string, 10 jobs, **no descriptions** |
| `get_job_details` | `job_id` | markdown with the full description |

Three properties of that API drive the design:

1. **`job_id` is positional** (`JOBSEARCH_3`) and scoped to the most recent
   search, so descriptions are fetched immediately after each search, before
   the next one renumbers them.
2. **The apply URL is a fresh tracking link on every call** — the same job came
   back as `aancjklz7zvl` then `aa4fhrtqlj7m` — so it cannot be a dedupe key.
   Title plus company is used instead.
3. **Ten results per call**, so the page issues one search per job title and
   merges, the same fan-out the phrase-only boards get.

Whether Indeed honours boolean through this API is **not established** — the
schema calls the field "job title or keywords". So the page sends a plain
title per call for maximum recall and applies your skills and exclusions
locally, exactly as the MyCareersFuture pipeline does. Exclusions are applied
before a description is fetched, so detail calls are not spent on noise.

A listing whose description could not be read is marked `summary only`, because
fit scoring on those has nothing but the title and company to work from. It is
never given invented text.

## Why the repository pipeline still exists

Indeed and MyCareersFuture do not cover the same ground, and only one of them
supplies structured data:

- **Indeed** — broad aggregation including agency reposts; on demand; no
  structured skill tags.
- **MyCareersFuture** — the government bank, more complete for roles covered by
  the Fair Consideration Framework, and it tags every listing with
  employer-chosen skill terms. Those tags are what the vocabulary miner counts.

Keep both. The connector answers "what is open right now"; the pipeline gives
depth and the keyword corpus.

## Why it is split this way

A published artifact cannot make any outbound network request of its own — the
content security policy blocks `fetch`, `XHR` and WebSockets to every host, and
Claude running inside the page has no web access either. The only way out is a
connector the viewer has installed (see above). For every board without a
connector, the page still cannot crawl, and any page that appears to is
fabricating.

Rather than pretend otherwise, sourcing is split:

- The **page** derives search terms from your evidence ledger and hands you
  pre-filled searches on ten Singapore boards, plus a `queries.json` you commit.
- The **workflow** does the actual retrieval, from a real API, on a schedule,
  and commits the results. You load that file into the page by hand.

## The anti-hallucination design

The requirement is that a tailored resume never claims something you did not do.
Four mechanisms, in order of how much they are trusted:

1. **Verbatim ledger (deterministic).** The resume is transcribed into atomic
   facts, each carrying the exact wording it came from. Every one is then
   checked back against your document. A fact whose wording is not actually
   there is marked `not in your document` and is **barred from every draft** —
   this catches the extraction step inventing things.
2. **Citation-only drafting.** Drafts are generated from the ledger, never from
   the raw resume, and every item must cite ledger ids. Employer names, job
   titles and dates are **not** generated at all — the page renders those
   directly from the ledger, which removes that class of error entirely.
3. **Deterministic audit.** Every generated line is re-checked in code:
   - a figure that is not in the cited lines, or nowhere in the resume at all;
   - a proper noun or tool name absent from your resume — distinguishing terms
     lifted from the job posting (amber: plausible but you must confirm) from
     terms in neither (red);
   - ownership inflation — `led`, `owned`, `drove` when the source says
     `assisted with`;
   - any item with no citation.
4. **Second reading.** A separate pass judges each line against the ledger only,
   returning `supported` / `overstated` / `unsupported`. It can disagree with
   the deterministic audit; both verdicts are shown.

Cover letters add one more rule: motivation cannot be derived from a resume, so
if you supply no reason for wanting the role, the letter leaves a marked
placeholder rather than inventing enthusiasm.

Unmet requirements are reported as **gaps**, and a "Not claimed" section lists
what the posting wanted that your evidence does not support. Giving unsupported
claims somewhere honest to go is what stops them leaking into the prose.

## Running the listings sync

```bash
python job_sourcing/fetch_listings.py
```

Reads `job_sourcing/queries.json`, writes `job_sourcing/data/listings.json`.
Stdlib only. The workflow in `.github/workflows/job-listings.yml` runs it daily
at 09:15 SGT and commits changes.

MyCareersFuture needs **no API key** — it is Singapore's government job bank and
its API is public. Under the Fair Consideration Framework most roles must be
advertised there before an Employment Pass application, so its coverage of local
hiring is unusually complete.

### Endpoint: confirmed

The first Actions run settled it. `POST https://api.mycareersfuture.gov.sg/v2/search`
answers with `{results: [...]}`, no key, no session — 630 listings across three
queries on the first run. The probe is retained so a future change fails over
rather than breaking.

Two things the search endpoint does **not** return, confirmed against the real
payload: a job **description**, and position levels / districts / minimum years.
Those come from the per-job detail endpoint, so the script hydrates the top
`hydrateTop` listings separately. Anything it cannot hydrate keeps an empty
description rather than being given invented text.

If nothing answers it **exits non-zero and writes nothing**. It never emits
placeholder listings. `data/_meta.json` records the working shape, the probe
errors, and a raw sample record so field mappings can be corrected against
real data instead of guessed at.

### Boolean search

MyCareersFuture takes a plain phrase — there is no boolean syntax. So the
pipeline fetches **broadly** on your titles and applies the boolean itself, over
the results:

```json
{
  "queries":  ["Business Analyst", "Senior Business Analyst"],
  "mustAny":  ["SQL", "Python", "Tableau"],
  "exclude":  ["intern", "insurance", "financial consultant"]
}
```

`mustAll` requires every term; `mustAny` requires one; `exclude` drops the
listing. Matching is over title, company, description, skills and categories.
Measured on the first real pull: 630 listings → 101 with the skills group → 92
after exclusions.

If the rules remove everything, the run **fails rather than committing an empty
feed**, so a too-narrow query is visible instead of silent.

### Query syntax differs by board

The page compiles one query model into whatever each board actually parses.
Feeding boolean to a board that does not support it returns noise, so `phrase`
is the default and boolean is only claimed where support is well established:

| Board | Syntax | Result |
|---|---|---|
| Indeed SG | full boolean + `title:` field | one precise query |
| LinkedIn | boolean, no field prefixes | one query, `NOT` for exclusions |
| All others | phrase only | one plain link **per title** |

### Scheduling

`schedule:` triggers only fire for workflows on the repository's **default
branch**. While this work sits on a feature branch the daily run will not
happen — it has only ever run on push. Merging the PR onto the default branch
starts the daily sync.

### Changing what is searched

Build the query in the page's Sourcing stage and export it — titles, a skills
group set to match-any or match-all, and exclusions — then commit the file.

The Sourcing stage also **mines the vocabulary of postings you have loaded**.
MyCareersFuture tags every listing with employer-chosen skill terms; the page
counts them and splits the result into terms your ledger can evidence and terms
it cannot. The first set belongs in your query and your resume, in the
employers' own spelling. The second is either a real skills gap or noise worth
excluding. On the first pull that was 1,486 distinct terms across 630 listings —
for "business analyst" in Singapore the top tags were `UAT` (67), `User Stories`
(57), `Stakeholder Management` (50), `Business Requirement Analysis` (41).

Salary figures are kept as **gross monthly SGD**, the Singapore convention, and
are not converted to an annual figure.

## Privacy

Your resume is held in your browser's local storage. It is sent to Claude to be
analysed. It is not written to the artifact's shared storage unless you tick the
backup option, which is off by default because resumes carry contact details and
shared storage is readable by anyone the artifact is shared with.
