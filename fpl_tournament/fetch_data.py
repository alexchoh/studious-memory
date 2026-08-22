#!/usr/bin/env python3
"""
Pulls data from the official Fantasy Premier League API for one mini-league,
ranks the managers head-to-head every gameweek (10/7/5/2/1 points, split
evenly across ties), accumulates a season leaderboard, and works out a few
"fun facts" per gameweek. Writes everything to data.json for render.py.

Safe to re-run at any time (e.g. hourly) - finished gameweeks are cached on
disk so a refresh only re-fetches the live/in-progress gameweek.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

LEAGUE_ID = 405116
BASE = "https://fantasy.premierleague.com/api"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "cache")
OUT_PATH = os.path.join(HERE, "data.json")

RANK_POINTS = [10, 7, 5, 2, 1]  # 1st .. 5th

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (fpl-tournament-dashboard)"})


def get(url, retries=3):
    for attempt in range(retries):
        r = session.get(url, timeout=20)
        if r.status_code == 200:
            return r.json()
        if attempt == retries - 1:
            r.raise_for_status()
        time.sleep(1.5 * (attempt + 1))


def cache_path(name):
    return os.path.join(CACHE_DIR, name)


def load_cached(name):
    p = cache_path(name)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return None


def save_cache(name, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache_path(name), "w") as f:
        json.dump(data, f)


def fetch_gw_dependent(name, url, finished):
    """Fetch data for a specific gameweek, caching it once that GW is finished
    (finished data is immutable; in-progress data is always re-fetched)."""
    if finished:
        cached = load_cached(name)
        if cached is not None:
            return cached
    data = get(url)
    if finished:
        save_cache(name, data)
    return data


def rank_and_score(gw_scores):
    """gw_scores: list of (entry_id, points). Returns {entry_id: tournament_points},
    splitting points evenly across ties (e.g. two teams tied for 2nd/3rd each
    get (7+5)/2 = 6)."""
    ordered = sorted(gw_scores, key=lambda x: -x[1])
    result = {}
    i = 0
    while i < len(ordered):
        j = i
        while j < len(ordered) and ordered[j][1] == ordered[i][1]:
            j += 1
        group = ordered[i:j]
        share = sum(RANK_POINTS[i:j]) / len(group)
        for entry_id, _pts in group:
            result[entry_id] = share
        i = j
    return result


def main():
    print("Fetching bootstrap-static...", file=sys.stderr)
    bootstrap = get(f"{BASE}/bootstrap-static/")
    events = bootstrap["events"]
    elements_by_id = {e["id"]: e for e in bootstrap["elements"]}
    teams_by_id = {t["id"]: t for t in bootstrap["teams"]}

    current_event = next((e["id"] for e in events if e["is_current"]), None)
    if current_event is None:
        # Pre-season / between seasons: fall back to the last finished event.
        finished = [e["id"] for e in events if e["finished"]]
        current_event = finished[-1] if finished else 1
    finished_map = {e["id"]: e["finished"] for e in events}
    deadline_map = {e["id"]: e["deadline_time"] for e in events}
    last_completed_event = max(
        [e["id"] for e in events if e["finished"]], default=0
    )

    print(f"Current event: {current_event}", file=sys.stderr)

    print("Fetching league standings...", file=sys.stderr)
    league = get(f"{BASE}/leagues-classic/{LEAGUE_ID}/standings/")
    league_name = league["league"]["name"]
    entries = [
        {
            "entry_id": r["entry"],
            "team_name": r["entry_name"],
            "manager_name": r["player_name"],
        }
        for r in league["standings"]["results"]
    ]

    # Per-entry season history (points/rank per GW, cheap single call each)
    for entry in entries:
        eid = entry["entry_id"]
        print(f"Fetching history for entry {eid}...", file=sys.stderr)
        hist = get(f"{BASE}/entry/{eid}/history/")
        entry["history_current"] = {h["event"]: h for h in hist["current"]}
        entry["chips_used"] = hist.get("chips", [])

    played_events = [e for e in range(1, current_event + 1)]

    # Live element data per played gameweek (for fun facts)
    live_by_event = {}
    for gw in played_events:
        finished = finished_map.get(gw, False)
        name = f"live_{gw}.json"
        finished_url = f"{BASE}/event/{gw}/live/"
        print(f"Fetching live data for GW{gw} (finished={finished})...", file=sys.stderr)
        live_by_event[gw] = fetch_gw_dependent(name, finished_url, finished)

    # Picks per entry per gameweek
    picks = {entry["entry_id"]: {} for entry in entries}
    for entry in entries:
        eid = entry["entry_id"]
        for gw in played_events:
            finished = finished_map.get(gw, False)
            name = f"picks_{eid}_{gw}.json"
            url = f"{BASE}/entry/{eid}/event/{gw}/picks/"
            try:
                data = fetch_gw_dependent(name, url, finished)
            except requests.HTTPError:
                # Entry may not have existed yet for this GW.
                continue
            picks[eid][gw] = data

    # --- Compute per-GW rankings & tournament points -------------------
    gameweeks = []
    season_totals = {e["entry_id"]: 0.0 for e in entries}
    season_gw_points = {e["entry_id"]: [] for e in entries}  # tournament pts per gw
    season_wins = {e["entry_id"]: 0 for e in entries}

    for gw in played_events:
        scores = []
        for entry in entries:
            eid = entry["entry_id"]
            hist = entry["history_current"].get(gw)
            if hist is None:
                continue
            scores.append((eid, hist["points"]))
        if not scores:
            continue

        tourney_points = rank_and_score(scores)
        ordered = sorted(scores, key=lambda x: -x[1])

        gw_entries = []
        for rank_idx, (eid, pts) in enumerate(ordered):
            entry = next(e for e in entries if e["entry_id"] == eid)
            hist = entry["history_current"][gw]
            entry_picks = picks[eid].get(gw, {})
            gw_entries.append(
                {
                    "entry_id": eid,
                    "team_name": entry["team_name"],
                    "manager_name": entry["manager_name"],
                    "gw_points": pts,
                    "tournament_points": tourney_points[eid],
                    "overall_rank": hist.get("overall_rank"),
                    "points_on_bench": hist.get("points_on_bench"),
                    "transfers": hist.get("event_transfers"),
                    "transfer_cost": hist.get("event_transfers_cost"),
                    "chip": entry_picks.get("active_chip"),
                    "captain": None,
                    "vice_captain": None,
                    "automatic_subs": len(entry_picks.get("automatic_subs", []) or []),
                }
            )
            season_totals[eid] += tourney_points[eid]
            season_gw_points[eid].append(tourney_points[eid])

            # captain / vice captain names
            for p in entry_picks.get("picks", []) or []:
                el = elements_by_id.get(p["element"])
                if not el:
                    continue
                if p.get("is_captain"):
                    gw_entries[-1]["captain"] = el["web_name"]
                if p.get("is_vice_captain"):
                    gw_entries[-1]["vice_captain"] = el["web_name"]

        # winner(s) of this GW = whoever's tournament_points == max
        top_score = max(tourney_points.values())
        winners = [
            e["team_name"] for e in gw_entries if e["tournament_points"] == top_score
        ]
        for w_name in winners:
            for e in entries:
                if e["team_name"] == w_name:
                    season_wins[e["entry_id"]] += 1 / len(winners)

        # --- fun facts for this GW ---
        live = live_by_event.get(gw, {}).get("elements", [])
        live_by_pid = {el["id"]: el["stats"] for el in live}

        # highest individual scorer across all 5 squads
        best_player = None  # (points, web_name, owner_team, is_captain)
        bench_waste = None  # (team_name, bench_points)
        differentials = []  # players owned by exactly one of the 5 squads
        owned_count = {}
        for entry in entries:
            entry_picks = picks[entry["entry_id"]].get(gw, {})
            for p in entry_picks.get("picks", []) or []:
                owned_count[p["element"]] = owned_count.get(p["element"], 0) + 1

        for entry in entries:
            eid = entry["entry_id"]
            entry_picks = picks[eid].get(gw, {})
            for p in entry_picks.get("picks", []) or []:
                el = elements_by_id.get(p["element"])
                stats = live_by_pid.get(p["element"])
                if not el or not stats:
                    continue
                raw_pts = stats.get("total_points", 0)
                effective_pts = raw_pts * p.get("multiplier", 1)
                if p.get("position", 0) <= 11:  # started (not bench)
                    if best_player is None or effective_pts > best_player[0]:
                        best_player = (
                            effective_pts,
                            el["web_name"],
                            entry["team_name"],
                            p.get("is_captain", False),
                        )
                    if owned_count.get(p["element"]) == 1 and raw_pts >= 5:
                        differentials.append(
                            (raw_pts, el["web_name"], entry["team_name"])
                        )

            bench_pts = entry["history_current"].get(gw, {}).get("points_on_bench")
            if bench_pts is not None and (
                bench_waste is None or bench_pts > bench_waste[1]
            ):
                bench_waste = (entry["team_name"], bench_pts)

        differentials.sort(key=lambda x: -x[0])

        fun_facts = {
            "best_player": (
                {
                    "points": best_player[0],
                    "name": best_player[1],
                    "team": best_player[2],
                    "captained": best_player[3],
                }
                if best_player
                else None
            ),
            "biggest_bench_waste": (
                {"team": bench_waste[0], "points": bench_waste[1]}
                if bench_waste
                else None
            ),
            "top_differential": (
                {
                    "points": differentials[0][0],
                    "name": differentials[0][1],
                    "team": differentials[0][2],
                }
                if differentials
                else None
            ),
        }

        gameweeks.append(
            {
                "gw": gw,
                "finished": finished_map.get(gw, False),
                "deadline": deadline_map.get(gw),
                "is_live": gw == current_event and not finished_map.get(gw, False),
                "entries": gw_entries,
                "fun_facts": fun_facts,
            }
        )

    # --- Season leaderboard ----------------------------------------------
    leaderboard = []
    for entry in entries:
        eid = entry["entry_id"]
        gw_hist = entry["history_current"]
        overall_total = gw_hist.get(current_event, {}).get(
            "total_points",
            gw_hist.get(last_completed_event, {}).get("total_points", 0),
        )
        leaderboard.append(
            {
                "entry_id": eid,
                "team_name": entry["team_name"],
                "manager_name": entry["manager_name"],
                "tournament_points": round(season_totals[eid], 2),
                "gw_history": season_gw_points[eid],
                "gw_wins": round(season_wins[eid], 2),
                "fpl_total_points": overall_total,
                "chips_used": [c["name"] for c in entry.get("chips_used", [])],
            }
        )
    leaderboard.sort(key=lambda x: (-x["tournament_points"], -x["fpl_total_points"]))
    for idx, row in enumerate(leaderboard):
        row["position"] = idx + 1

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "league_id": LEAGUE_ID,
        "league_name": league_name,
        "current_event": current_event,
        "current_event_finished": finished_map.get(current_event, False),
        "total_events": len(events),
        "leaderboard": leaderboard,
        "gameweeks": list(reversed(gameweeks)),  # most recent first
        "scoring_table": {
            "1st": RANK_POINTS[0],
            "2nd": RANK_POINTS[1],
            "3rd": RANK_POINTS[2],
            "4th": RANK_POINTS[3],
            "5th": RANK_POINTS[4],
        },
    }

    with open(OUT_PATH, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {OUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
