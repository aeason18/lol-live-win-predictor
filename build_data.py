"""Learn item builds from ranked games and write data/builds.json for the overlay.

    py build_data.py --region na1 --matches 3000

Needs a Riot API key in a .env file next to this script:  RIOT_API_KEY=RGAPI-...
A 24-hour development key is fine; the overlay never calls Riot's web API, only this
script does. It is resumable: re-run it with a fresh key and it carries on where it
stopped. Use --aggregate-only to rebuild builds.json from the games already collected.

How the builds are chosen. A late item has a high win rate partly because only players who
are already winning can afford it, so items are never compared across the whole game.
Instead each build slot (1st completed item, 2nd, ... 6th, plus boots and starting items)
is ranked only against the other choices for that same slot, and the win rate is pulled
toward that slot's average so a lucky handful of games can't win.
"""

import argparse
import json
import os
import random
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

from lol_items import BUILD_SLOTS, Catalog

HERE = Path(__file__).parent
DATA = HERE / "data"
OUT_FILE = DATA / "builds.json"

QUEUE_RANKED_SOLO = 420
ROLES = {"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"}
START_WINDOW_MS = 45_000  # items bought this soon after the game starts are "starting items"
MIN_GAME_SECONDS = 900  # skip remakes and early surrenders

# Ranking knobs
MIN_GAMES = 25  # games a champion+role needs before it gets a build
MIN_SHARE = 0.05  # an item must be chosen by at least this share of players at its slot
MIN_PICKS = 5
PRIOR = 20  # games' worth of the slot's average win rate mixed into every item
TOP_N = 3  # candidates kept per slot, so situational swaps are possible later

ROUTING = {
    "na1": "americas", "br1": "americas", "la1": "americas", "la2": "americas",
    "euw1": "europe", "eun1": "europe", "tr1": "europe", "ru": "europe",
    "kr": "asia", "jp1": "asia",
    "oc1": "sea", "ph2": "sea", "sg2": "sea", "th2": "sea", "tw2": "sea", "vn2": "sea",
}
QUEUE = "RANKED_SOLO_5x5"
APEX = {"CHALLENGER": "challengerleagues", "GRANDMASTER": "grandmasterleagues", "MASTER": "masterleagues"}


# --- Riot API ---------------------------------------------------------------
class Riot:
    """Minimal client that stays under a development key's limits (100 calls / 2 minutes)."""

    def __init__(self, key, region, min_interval=1.25):
        self.session = requests.Session()
        self.session.headers["X-Riot-Token"] = key
        self.platform = f"https://{region}.api.riotgames.com"
        self.regional = f"https://{ROUTING[region]}.api.riotgames.com"
        self.min_interval = min_interval
        self._last = 0.0

    def get(self, base, path, **params):
        for attempt in range(5):
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            resp = self.session.get(f"{base}{path}", params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                pause = int(resp.headers.get("Retry-After", 10)) + 1
                print(f"  rate limited, waiting {pause}s")
                time.sleep(pause)
            elif resp.status_code in (401, 403):
                raise SystemExit("Riot rejected the API key (expired or wrong). Update RIOT_API_KEY in .env and re-run; "
                                 "progress so far is saved.")
            elif resp.status_code == 404:
                return None
            elif resp.status_code >= 500:
                time.sleep(2 * (attempt + 1))
            else:
                raise SystemExit(f"Unexpected {resp.status_code} from {path}: {resp.text[:200]}")
        return None


def load_key():
    key = os.environ.get("RIOT_API_KEY")
    env = HERE / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("RIOT_API_KEY"):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        raise SystemExit("No API key. Create a .env file next to build_data.py containing: RIOT_API_KEY=RGAPI-...")
    return key


def seed_puuids(riot, tiers, want):
    """Player ids from the ranked ladder, shuffled so no tier or division dominates."""
    puuids = []
    per_tier = -(-want // len(tiers))  # ceiling division: each tier's share of the players
    for tier in tiers:
        if tier in APEX:
            league = riot.get(riot.platform, f"/lol/league/v4/{APEX[tier]}/by-queue/{QUEUE}") or {}
            entries = league.get("entries", [])
        else:
            entries = []
            # Most ladder accounts are inactive, so page through divisions until the tier's share is met.
            for division in ("I", "II", "III", "IV"):
                for page in range(1, 50):
                    batch = riot.get(riot.platform, f"/lol/league/v4/entries/{QUEUE}/{tier}/{division}", page=page)
                    if not batch:
                        break
                    entries += batch
                    if len(entries) >= per_tier:
                        break
                if len(entries) >= per_tier:
                    break
        found = [e["puuid"] for e in entries if e.get("puuid")]
        if entries and not found:
            raise SystemExit("The ladder response had no 'puuid' field, so players can't be looked up. "
                             "Riot may have changed the league-v4 response; the script needs updating.")
        random.shuffle(found)
        print(f"  {tier}: {len(found[:per_tier])} players")
        puuids += found[:per_tier]
    random.shuffle(puuids)
    return puuids


# --- turning a match into per-player build records ---------------------------
def extract_records(match, timeline, catalog):
    """One record per player: role, win, starting items, completed items in order, boots."""
    info = match["info"]
    if info.get("queueId") != QUEUE_RANKED_SOLO or info.get("gameDuration", 0) < MIN_GAME_SECONDS:
        return []
    patch = ".".join(info["gameVersion"].split(".")[:2])
    state = {p["participantId"]: {"start": [], "legendary": [], "boots": 0, "boots_after": 0}
             for p in info["participants"]}
    events = [e for f in timeline["info"]["frames"] for e in f["events"]
              if e["type"] in ("ITEM_PURCHASED", "ITEM_SOLD", "ITEM_UNDO")]
    events.sort(key=lambda e: e["timestamp"])

    for e in events:
        s = state.get(e["participantId"])
        if s is None:
            continue
        if e["type"] == "ITEM_PURCHASED":
            item = e["itemId"]
            if e["timestamp"] <= START_WINDOW_MS and not catalog.is_trinket(item):
                s["start"].append(item)
            if catalog.is_legendary(item):
                s["legendary"].append(item)
            elif catalog.is_boots(item):
                s["boots"], s["boots_after"] = item, len(s["legendary"])
        elif e["type"] == "ITEM_SOLD":  # selling a completed item reopens its slot; starters sold later still count
            if e["itemId"] in s["legendary"]:
                s["legendary"].remove(e["itemId"])
        else:  # ITEM_UNDO: the purchase never happened, wherever it was
            item = e.get("beforeId")
            for lst in (s["start"], s["legendary"]):
                if item in lst:
                    lst.remove(item)
                    break
            if s["boots"] == item:
                s["boots"] = 0

    records = []
    for p in info["participants"]:
        role = p.get("teamPosition")
        s = state[p["participantId"]]
        if role not in ROLES or not p.get("championName"):
            continue
        records.append({
            "champ": p["championName"], "role": role, "win": int(p["win"]), "patch": patch,
            "start": sorted(s["start"]), "legendary": s["legendary"][:BUILD_SLOTS],
            "boots": s["boots"], "boots_after": s["boots_after"],
        })
    return records


# --- ranking ------------------------------------------------------------------
def _rank(picks):
    """picks: [(choice, won)] for one slot -> the best few choices, ranked within that slot."""
    n, w = Counter(), Counter()
    for choice, won in picks:
        n[choice] += 1
        w[choice] += won
    total = sum(n.values())
    if total == 0:
        return []
    slot_wr = sum(w.values()) / total
    rows = [{"key": c, "n": n[c], "wr": round(w[c] / n[c], 4),
             "score": round((w[c] + PRIOR * slot_wr) / (n[c] + PRIOR), 4)} for c in n]
    kept = [r for r in rows if r["n"] >= max(MIN_PICKS, MIN_SHARE * total)]
    if not kept:  # tiny sample: fall back to what was picked most
        kept = sorted(rows, key=lambda r: -r["n"])[:1]
    return sorted(kept, key=lambda r: -r["score"])[:TOP_N]


def aggregate(records):
    """Build entries per champion and role from per-player records."""
    groups = defaultdict(list)
    for r in records:
        groups[(r["champ"], r["role"])].append(r)

    champions = defaultdict(dict)
    for (champ, role), rs in groups.items():
        if len(rs) < MIN_GAMES:
            continue
        slots = []
        for k in range(BUILD_SLOTS):
            ranked = _rank([(r["legendary"][k], r["win"]) for r in rs if len(r["legendary"]) > k])
            slots.append([{"id": x["key"], "n": x["n"], "wr": x["wr"], "score": x["score"]} for x in ranked])
        with_boots = [r for r in rs if r["boots"]]
        boots = {"after": 0, "items": []}
        if with_boots:
            boots["after"] = int(round(statistics.median(r["boots_after"] for r in with_boots)))
            boots["items"] = [{"id": x["key"], "n": x["n"], "wr": x["wr"], "score": x["score"]}
                              for x in _rank([(r["boots"], r["win"]) for r in with_boots])]
        start = [{"items": list(x["key"]), "n": x["n"], "wr": x["wr"], "score": x["score"]}
                 for x in _rank([(tuple(r["start"]), r["win"]) for r in rs if r["start"]])]
        champions[champ][role] = {
            "games": len(rs), "wr": round(sum(r["win"] for r in rs) / len(rs), 4),
            "start": start, "boots": boots, "slots": slots,
        }
    return dict(champions)


def write_builds(records, region):
    patch = Counter(r["patch"] for r in records).most_common(1)[0][0]  # builds only make sense within one patch
    current = [r for r in records if r["patch"] == patch]
    builds = {
        "patch": patch, "region": region, "games": len(current),
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "champions": aggregate(current),
    }
    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(json.dumps(builds, separators=(",", ":")), encoding="utf-8")
    covered = sum(len(v) for v in builds["champions"].values())
    print(f"Wrote {OUT_FILE.name}: patch {patch}, {len(current)} player-games, "
          f"{len(builds['champions'])} champions, {covered} champion/role builds")


# --- collection ---------------------------------------------------------------
def collect(riot, catalog, region, target, tiers, days, per_player):
    raw = DATA / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    records_file, seen_file = raw / f"records_{region}.jsonl", raw / f"seen_{region}.txt"
    seen = set(seen_file.read_text().split()) if seen_file.exists() else set()
    print(f"Resuming with {len(seen)} matches already collected." if seen else "Starting fresh.")

    print("Finding players on the ladder...")
    # About two matches turn up per player in a week, once overlaps and inactive accounts are counted.
    players = seed_puuids(riot, tiers, want=max(100, target))
    since = int(time.time()) - days * 86400
    with records_file.open("a", encoding="utf-8") as rf, seen_file.open("a") as sf:
        for i, puuid in enumerate(players, 1):
            if len(seen) >= target:
                break
            ids = riot.get(riot.regional, f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
                           queue=QUEUE_RANKED_SOLO, startTime=since, count=per_player) or []
            for match_id in ids:
                if match_id in seen or len(seen) >= target:
                    continue
                match = riot.get(riot.regional, f"/lol/match/v5/matches/{match_id}")
                timeline = match and riot.get(riot.regional, f"/lol/match/v5/matches/{match_id}/timeline")
                seen.add(match_id)
                sf.write(match_id + "\n")
                if match and timeline:
                    for rec in extract_records(match, timeline, catalog):
                        rf.write(json.dumps(rec) + "\n")
                if len(seen) % 25 == 0:
                    rf.flush(), sf.flush()
                    print(f"  {len(seen)}/{target} matches ({i}/{len(players)} players)")


def load_records(region):
    path = DATA / "raw" / f"records_{region}.jsonl"
    if not path.exists():
        raise SystemExit(f"No collected games for {region} yet. Run without --aggregate-only first.")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default="na1", choices=sorted(ROUTING), help="platform, e.g. na1, euw1, kr")
    ap.add_argument("--matches", type=int, default=3000, help="how many matches to collect in total")
    ap.add_argument("--tiers", nargs="+", default=["EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"])
    ap.add_argument("--days", type=int, default=7, help="only use games from the last N days (builds are per patch)")
    ap.add_argument("--per-player", type=int, default=10, help="recent games to take from each player")
    ap.add_argument("--aggregate-only", action="store_true", help="skip collection; rebuild builds.json")
    args = ap.parse_args()

    catalog = Catalog.load()
    print(f"Item data: patch {catalog.version}")
    if not args.aggregate_only:
        riot = Riot(load_key(), args.region)
        collect(riot, catalog, args.region, args.matches, args.tiers, args.days, args.per_player)
    write_builds(load_records(args.region), args.region)


if __name__ == "__main__":
    main()
