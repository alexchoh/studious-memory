#!/usr/bin/env python3
"""
Decides when the dashboard should next refresh, based on live Premier League
fixture state, and prints that RFC3339 timestamp to stdout (reason to
stderr). The refresh routine reads this after each run and reschedules
itself via update_trigger(run_once_at=<that timestamp>) - so cadence tracks
matches instead of a fixed cron:

  - a fixture is live right now              -> check again in 10 minutes
  - the gameweek's last fixture just finished -> one final check 2h later
  - mid-gameweek lull (some played, more to come, none live) -> back off,
    but not longer than a few hours, and always in time for the next kickoff
  - the gameweek is fully wrapped up (final update already sent) -> daily,
    until the next gameweek's kickoff is close, then ramp back up

State (which gameweek we were last watching, and whether its final update
has already gone out) persists in refresh_state.json so the "just finished"
transition is only caught once.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "refresh_state.json")
BASE = "https://fantasy.premierleague.com/api"

LIVE_INTERVAL = timedelta(minutes=10)
FINAL_UPDATE_DELAY = timedelta(hours=2)
LULL_MAX_BACKOFF = timedelta(hours=3)
PRE_KICKOFF_LEAD = timedelta(minutes=20)
DAILY_BACKOFF = timedelta(hours=24)
RAMP_UP_WINDOW = timedelta(hours=20)


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"last_active_gw": None, "final_update_done_for_gw": None}


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def parse_kickoff(f):
    return datetime.fromisoformat(f["kickoff_time"].replace("Z", "+00:00"))


def fetch_events(session, event_ids):
    """Fetch fixtures for specific gameweeks via the per-event endpoint.

    The bulk /api/fixtures/ (whole-season) endpoint is cached more
    aggressively upstream and has been observed serving a stale
    started=false for a fixture that had already kicked off - the
    per-event endpoint reflects live state promptly, so that's what
    scheduling decisions must be based on."""
    fixtures = []
    for event_id in event_ids:
        if event_id < 1 or event_id > 38:
            continue
        fixtures.extend(session.get(f"{BASE}/fixtures/?event={event_id}", timeout=20).json())
    return fixtures


def main():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (fpl-tournament-dashboard)"})
    now = datetime.now(timezone.utc)
    state = load_state()

    anchor = state.get("last_active_gw")
    if anchor is None:
        bootstrap = session.get(f"{BASE}/bootstrap-static/", timeout=20).json()
        anchor = next((e["id"] for e in bootstrap["events"] if e["is_current"]), None)
        if anchor is None:
            anchor = next((e["id"] for e in bootstrap["events"] if e["is_next"]), 1)

    fixtures = fetch_events(session, [anchor, anchor + 1])

    def unfinished_map(fixture_list):
        m = {}
        for f in fixture_list:
            if not f.get("finished_provisional"):
                m.setdefault(f["event"], []).append(f)
        return m

    unfinished_by_event = unfinished_map(fixtures)

    if not unfinished_by_event:
        # Our anchor window is fully finished - state was stale (e.g. this
        # routine was paused for a while). Re-seed from bootstrap-static
        # instead of assuming the season is over.
        bootstrap = session.get(f"{BASE}/bootstrap-static/", timeout=20).json()
        fresh_anchor = next((e["id"] for e in bootstrap["events"] if e["is_current"]), None)
        if fresh_anchor is None:
            fresh_anchor = next((e["id"] for e in bootstrap["events"] if e["is_next"]), None)
        if fresh_anchor is not None and fresh_anchor != anchor:
            fixtures = fetch_events(session, [fresh_anchor, fresh_anchor + 1])
            unfinished_by_event = unfinished_map(fixtures)

    active_gw = min(unfinished_by_event.keys()) if unfinished_by_event else None
    prev_active_gw = state.get("last_active_gw")

    # Did the gameweek we were previously watching just wrap up completely?
    if (
        prev_active_gw is not None
        and prev_active_gw != active_gw
        and state.get("final_update_done_for_gw") != prev_active_gw
    ):
        next_run = now + FINAL_UPDATE_DELAY
        reason = f"final wrap-up update for gameweek {prev_active_gw}"
        state["final_update_done_for_gw"] = prev_active_gw
        state["last_active_gw"] = active_gw
        save_state(state)
        emit(next_run, reason)
        return

    state["last_active_gw"] = active_gw

    if active_gw is None:
        next_run = now + DAILY_BACKOFF
        reason = "no upcoming fixtures found (season finished or not yet scheduled)"
        save_state(state)
        emit(next_run, reason)
        return

    gw_fixtures = unfinished_by_event[active_gw]
    live_now = [f for f in gw_fixtures if f.get("started")]

    if live_now:
        next_run = now + LIVE_INTERVAL
        reason = f"{len(live_now)} match(es) live in gameweek {active_gw}"
        save_state(state)
        emit(next_run, reason)
        return

    next_kickoff = min(parse_kickoff(f) for f in gw_fixtures)
    till_kickoff = next_kickoff - now

    if till_kickoff <= PRE_KICKOFF_LEAD:
        next_run = max(next_kickoff, now + timedelta(minutes=1))
        reason = f"kickoff imminent in gameweek {active_gw}"
    elif till_kickoff <= RAMP_UP_WINDOW:
        next_run = next_kickoff - PRE_KICKOFF_LEAD
        reason = f"ramping up ahead of the next kickoff in gameweek {active_gw}"
    elif state.get("final_update_done_for_gw") == active_gw - 1:
        # previous gameweek is fully wrapped and its final update already sent
        next_run = min(now + DAILY_BACKOFF, next_kickoff - PRE_KICKOFF_LEAD)
        reason = "daily check during the lull between gameweeks"
    else:
        # mid-gameweek lull: some fixtures played, more to come, nothing live now
        next_run = min(now + LULL_MAX_BACKOFF, next_kickoff - PRE_KICKOFF_LEAD)
        reason = f"lull between fixtures in gameweek {active_gw}"

    save_state(state)
    emit(next_run, reason)


def emit(next_run, reason):
    print(next_run.strftime("%Y-%m-%dT%H:%M:%SZ"))
    print(reason, file=sys.stderr)


if __name__ == "__main__":
    main()
