# FPL Tournament Leaderboard

Turns our FPL mini-league (id `405116`, "No Gueslist League") into a season-long
head-to-head tournament: every gameweek the 5 managers are ranked by GW points
and scored 10 / 7 / 5 / 2 / 1 (ties split the points evenly across the tied
ranks). Points accumulate into a season leaderboard.

Published dashboard: https://claude.ai/code/artifact/ab306cf8-ea32-472e-8d04-c92dda6b5c37

## How it works

- `fetch_data.py` pulls bootstrap, league standings, per-entry history, picks
  and live element data from the official FPL API
  (`https://fantasy.premierleague.com/api/...`), computes rankings/tournament
  points/fun facts, and writes `data.json`. Finished gameweeks are cached under
  `cache/` so a refresh only re-fetches the live/in-progress gameweek.
- `render.py` injects `data.json` into `template.html`, producing the final
  self-contained `dashboard.html` that gets published as the Claude Artifact.
- `schedule_next.py` looks at live fixture state and decides when the next
  refresh should happen, persisting a little state in `refresh_state.json`:
  - a match is live right now → check again in **10 minutes**
  - a gameweek's last fixture just finished → **one final check 2 hours
    later** (to catch confirmed bonus points, price changes, etc.)
  - mid-gameweek lull (some fixtures played, more to come, nothing live) →
    back off, but never longer than ~3 hours, and always in time for the
    next kickoff
  - gameweek fully wrapped up → back off to **daily** checks, ramping back
    up to a tighter cadence as the next gameweek's kickoff approaches
- A self-rescheduling Routine ties it together: each firing runs
  `fetch_data.py` → `render.py` → republishes the artifact → runs
  `schedule_next.py` → reschedules its own next firing via
  `update_trigger(run_once_at=...)` with whatever that script printed.

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
