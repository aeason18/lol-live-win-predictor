"""Snapshot a live League of Legends game at the 10 and 15 minute marks.

Run it before or during a game (the game client must be in-game):
    python3 lol_snapshots.py --rank GOLD

Writes two rows per game (minute 10 and minute 15) to lol_snapshots.csv next to
this script, using the same columns as data/lol_10min_*.csv plus a `minute`
column. Data comes from Riot's Live Client Data API, served locally by the game
at https://127.0.0.1:2999.

Not available from the live API, so left blank: gold, xp, plates, wards.
blue_win is filled once the game ends (blank if the script stops before then).

At each snapshot it also prints each team's win chance from the models in
win_model.json (built in lol_eda_10_vs_15.ipynb): an early read at 10 minutes and
a firmer one at 15, along with how much it moved.
"""

import argparse
import csv
import json
import math
import re
import time
from collections import Counter
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"
OUT_FILE = Path(__file__).with_name("lol_snapshots.csv")
MODEL_FILE = Path(__file__).with_name("win_model.json")
MARKS = [10, 15]  # game minutes
POLL_SECONDS = 1
LATE_SECONDS = 60  # skip the prediction if the snapshot was taken this long after its mark

STATS = [
    "gold", "xp", "level_sum", "cs", "kills", "assists", "wards",
    "towers", "plates", "dragons", "heralds", "voidgrubs",
]
FIELDS = (
    ["match_id", "rank_tier", "minute"]
    + [f"{side}_{s}" for s in STATS for side in ("blue", "red")]
    + [f"{s}_diff" for s in ("gold", "xp", "level_sum", "cs", "kills", "assists",
                            "wards", "towers", "plates", "dragons", "heralds", "voidgrubs")]
    + ["blue_first_blood", "blue_win"]
)
UNAVAILABLE = {"gold", "xp", "plates", "wards"}


def fetch(quiet=False):
    """Return the game JSON, or None if no game is running."""
    try:
        resp = requests.get(URL, verify=False, timeout=3)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        if not quiet:
            print(f"Could not read game data: {exc!r}")
        return None


def team_lookup(players):
    """Map every name an event might use for a player to ORDER/CHAOS."""
    lookup = {}
    for p in players:
        for key in ("riotId", "riotIdGameName", "summonerName"):
            if p.get(key):
                lookup[p[key]] = p["team"]
    return lookup


def snapshot(data, minute, match_id, rank):
    players = data["allPlayers"]
    lookup = team_lookup(players)
    game_time = data["gameData"]["gameTime"]
    events = [e for e in data["events"]["Events"] if e["EventTime"] <= game_time]

    def side(team):
        return "blue" if team == "ORDER" else "red"

    row = dict.fromkeys(FIELDS)
    row.update(match_id=match_id, rank_tier=rank, minute=minute)
    totals = {s: Counter() for s in STATS}

    for p in players:
        sc = p.get("scores", {})
        s = side(p["team"])
        totals["level_sum"][s] += p.get("level", 0)
        totals["cs"][s] += sc.get("creepScore", 0)

    def other(team):
        return "red" if team == "ORDER" else "blue"

    # Kills, assists, first blood and towers are counted the way the 10-min dataset
    # notebook counts them, so live rows line up with data/lol_10min_*.csv.
    event_stat = {"DragonKill": "dragons", "HeraldKill": "heralds", "HordeKill": "voidgrubs"}
    first_blood = None
    for e in events:
        name = e["EventName"]
        killer_team = lookup.get(e.get("KillerName"))
        if name == "ChampionKill":
            victim_team = lookup.get(e.get("VictimName"))
            if victim_team:
                # Credit the victim's enemies, so executions (turret/minion kills) count too.
                totals["kills"][other(victim_team)] += 1
                if first_blood is None:
                    first_blood = other(victim_team)
            for a in e.get("Assisters", []):
                if lookup.get(a):
                    totals["assists"][side(lookup[a])] += 1
        elif name in event_stat and killer_team:
            totals[event_stat[name]][side(killer_team)] += 1
        elif name == "TurretKilled":
            # Turret_TOrder_* is blue's tower, so its death is a red tower kill (and vice versa).
            owner = re.search(r"_T(Order|Chaos)_", e.get("TurretKilled", ""))
            if owner:
                totals["towers"]["red" if owner.group(1) == "Order" else "blue"] += 1
            elif killer_team:
                totals["towers"][side(killer_team)] += 1

    for s in STATS:
        if s in UNAVAILABLE:
            continue
        row[f"blue_{s}"] = totals[s]["blue"]
        row[f"red_{s}"] = totals[s]["red"]
        row[f"{s}_diff"] = totals[s]["blue"] - totals[s]["red"]
    # Blank when nobody has first blood yet, as in the 10-min dataset.
    if first_blood:
        row["blue_first_blood"] = int(first_blood == "blue")
    return row


def load_models():
    """{minute: model} from win_model.json, or {} if it's missing."""
    try:
        data = json.loads(MODEL_FILE.read_text(encoding="utf-8"))
        # Accept {"models": [...]} or a single bare model dict.
        models = data["models"] if "models" in data else [data]
        return {m["minute"]: m for m in models}
    except (OSError, ValueError, KeyError) as exc:
        print(f"No win-chance predictions: could not load {MODEL_FILE.name} ({exc!r})")
        return {}


def blue_win_chance(row, model):
    """Logistic-regression win chance for blue from a snapshot row."""
    z = model["intercept"]
    for f, mean, std, coef in zip(model["features"], model["mean"], model["std"], model["coef"]):
        value = row[f]
        if value is None:
            value = 0.5  # no first blood yet: neutral
        z += coef * (value - mean) / std
    return 1 / (1 + math.exp(-z))


def my_team(data):
    """ORDER/CHAOS for the player running the script, or None (e.g. spectating)."""
    me = data["activePlayer"].get("riotId") or data["activePlayer"].get("summonerName")
    return team_lookup(data["allPlayers"]).get(me)


def blue_won(data):
    """1/0 from the GameEnd event, or None if the game hasn't ended."""
    end = next((e for e in data["events"]["Events"] if e["EventName"] == "GameEnd"), None)
    if end is None:
        return None
    team = my_team(data)
    if team is None:
        return None
    won = end.get("Result") == "Win"
    return int(won == (team == "ORDER"))


def print_prediction(minute, blue, data, earlier=None):
    """Print the win chance; `earlier` is (minute, blue chance) from a previous snapshot."""
    label = "early read" if minute == min(MARKS) else "firmer read"
    print(f"Win chance at {minute} min ({label}): blue {blue:.0%}, red {1 - blue:.0%}")
    team = my_team(data)
    if team:
        mine = blue if team == "ORDER" else 1 - blue
        line = f"  Your team ({'blue' if team == 'ORDER' else 'red'}): {mine:.0%}"
        if earlier:
            was = earlier[1] if team == "ORDER" else 1 - earlier[1]
            line += f" (was {was:.0%} at {earlier[0]} min, {(mine - was) * 100:+.0f} points)"
        print(line)
    elif earlier:
        print(f"  Blue was {earlier[1]:.0%} at {earlier[0]} min ({(blue - earlier[1]) * 100:+.0f} points)")


def write_rows(rows):
    new_file = not OUT_FILE.exists()
    with OUT_FILE.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rank", default="", help="rank_tier label, e.g. GOLD")
    rank = parser.parse_args().rank.upper()
    models = load_models()
    last_chance = None  # (minute, blue win chance) from the previous snapshot

    match_id = time.strftime("LIVE_%Y%m%d_%H%M%S")
    pending = list(MARKS)
    rows, event_names, result, all_events = [], set(), None, []
    print("Waiting for a live game...")
    try:
        while True:
            data = fetch()
            if data is None:
                if rows:
                    break  # game closed after we got at least one snapshot
                time.sleep(5)
                continue
            all_events = data["events"]["Events"]
            event_names.update(e["EventName"] for e in all_events)
            game_time = data["gameData"]["gameTime"]
            if pending and game_time >= pending[0] * 60:
                minute = pending.pop(0)
                rows.append(snapshot(data, minute, match_id, rank))
                print(f"Captured {minute}-minute snapshot (game time {game_time:.0f}s)")
                if minute in models:
                    if game_time - minute * 60 <= LATE_SECONDS:
                        blue = blue_win_chance(rows[-1], models[minute])
                        print_prediction(minute, blue, data, last_chance)
                        last_chance = (minute, blue)
                    else:
                        print(f"No win chance: snapshot was taken {game_time / 60:.1f} min in, "
                              f"too long after the {minute}:00 mark the model is trained for.")
            result = blue_won(data)
            if result is not None:
                break
            if pending == []:
                print("Both snapshots taken; waiting for the game to end to record the winner...")
                pending = None
            time.sleep(5 if pending is None else POLL_SECONDS)
    except KeyboardInterrupt:
        print("Interrupted; saving what was captured.")
    finally:
        for r in rows:
            r["blue_win"] = result
        if rows:
            write_rows(rows)
            print(f"Saved {len(rows)} row(s) to {OUT_FILE}")
        else:
            print("No snapshots captured.")
        if all_events:
            events_file = OUT_FILE.with_name(f"{match_id}_events.json")
            events_file.write_text(json.dumps(all_events, indent=1), encoding="utf-8")
            print(f"Raw events saved to {events_file}")
        print("Event types seen this game:", ", ".join(sorted(event_names)))


if __name__ == "__main__":
    main()
