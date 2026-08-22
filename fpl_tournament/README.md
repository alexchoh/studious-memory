# FPL Tournament Leaderboard

Turns our FPL mini-league (id `405116`, "No Gueslist League") into a season-long
head-to-head tournament: every gameweek the 5 managers are ranked by GW points
and scored 10 / 7 / 5 / 2 / 1 (ties split the points evenly across the tied
ranks). Points accumulate into a season leaderboard.

Live dashboard: **https://alexchoh.github.io/studious-memory/** (GitHub Pages,
auto-refreshed by `.github/workflows/refresh-dashboard.yml` every 10 minutes)

Previously hosted as a Claude Artifact — that link still exists but is no
longer kept up to date:
https://claude.ai/code/artifact/ab306cf8-ea32-472e-8d04-c92dda6b5c37

## How it works

- `fetch_data.py` pulls bootstrap, league standings, per-entry history, picks
  and live element data from the official FPL API
  (`https://fantasy.premierleague.com/api/...`), computes rankings/tournament
  points/fun facts, and writes `data.json`. Finished gameweeks are cached under
  `cache/` so a refresh only re-fetches the live/in-progress gameweek.
- `render.py` injects `data.json` into `template.html`, producing the final
  self-contained `dashboard.html`.
- **`.github/workflows/refresh-dashboard.yml`** runs on a flat 10-minute cron
  (`workflow_dispatch` also works for an on-demand run): checks out the repo,
  runs `fetch_data.py` → `render.py`, copies `dashboard.html` to
  `_site/index.html`, and deploys it to GitHub Pages via
  `actions/upload-pages-artifact` + `actions/deploy-pages`. The
  `fpl_tournament/cache/` directory persists between runs via `actions/cache`
  so finished gameweeks aren't re-fetched every 10 minutes for the whole
  season. A flat interval (rather than the live/lull/daily backoff an earlier
  version used) is deliberate: GitHub Actions minutes on GitHub-hosted
  runners are free and unlimited for public repos, so there's no cost
  pressure to back off between gameweeks.
- `schedule_next.py` is no longer used by the live site (kept for reference /
  local manual runs) — it drove the old Claude-session-based refresh loop
  before the GitHub Pages migration.

## Manual refresh

```
cd fpl_tournament
python3 fetch_data.py
python3 render.py
```

Then republish `dashboard.html` to the existing artifact URL above.

## Scoring rule

| Rank | Points |
|------|--------|
| 1st  | 10 |
| 2nd  | 7 |
| 3rd  | 5 |
| 4th  | 2 |
| 5th  | 1 |

Ties split the combined points of the ranks they occupy evenly (e.g. two teams
tied for 2nd/3rd each get `(7+5)/2 = 6`).
