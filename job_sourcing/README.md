# Job sourcing and resume provenance

Two halves that work together:

| Part | What it is | Where it runs |
|---|---|---|
| `index.html` | The **Resume Provenance Desk** page — resume analysis, query building, fit scoring, and cited drafting | Published as a Claude Artifact |
| `fetch_listings.py` + workflow | A daily pull of **real** job listings from the MyCareersFuture public API | GitHub Actions |

## Why it is split this way

A published artifact cannot make any outbound network request — the content
security policy blocks `fetch`, `XHR` and WebSockets to every host, and Claude
running inside the page has no web access either. So a page of this kind
**cannot crawl job boards**, and any page that appears to is fabricating.

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

### A caveat about the endpoint

The API is public but undocumented, and published references disagree on its
shape (`GET /v2/jobs` vs `POST /v2/search`, and two hostnames). The sandbox this
was written in blocks egress to that domain, **so the working shape was never
confirmed here.** The script therefore probes a ranked list of candidates,
records the one that answers in `data/_meta.json`, and reuses it.

If none answers it **exits non-zero and writes nothing**. It will never emit
placeholder listings — a resume tailored against an invented posting is worse
than no listings at all. Check the first Actions run: `_meta.json` records which
shape worked and the errors from the ones that did not.

### Changing what is searched

Build the query set in the page's Sourcing stage and export it, or edit
`queries.json` directly:

```json
{
  "market": "Singapore",
  "maxPagesPerQuery": 3,
  "postedWithinDays": 30,
  "queries": ["data analyst", "business analyst"]
}
```

Salary figures are kept as **gross monthly SGD**, the Singapore convention, and
are not converted to an annual figure.

## Privacy

Your resume is held in your browser's local storage. It is sent to Claude to be
analysed. It is not written to the artifact's shared storage unless you tick the
backup option, which is off by default because resumes carry contact details and
shared storage is readable by anyone the artifact is shared with.
