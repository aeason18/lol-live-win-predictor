"""Item data (Riot Data Dragon) and next-item logic for the overlay.

Data Dragon is Riot's free static data: item names, recipes, gold costs and icons. It is
cached under data/ddragon/ so a game never waits on the network.

The build data that says *which* items to buy comes from build_data.py (data/builds.json).
"""

import json
import re
from collections import Counter
from pathlib import Path

import requests
from PIL import Image

DDRAGON = "https://ddragon.leagueoflegends.com"
CACHE = Path(__file__).with_name("data") / "ddragon"
SUMMONERS_RIFT = "11"
LEGENDARY_MIN_GOLD = 2000  # completed items start around here; components and epics are cheaper
BOOTS_MIN_GOLD = 900  # tier-2 boots and up; plain Boots (300g) don't count
BUILD_SLOTS = 6


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _fetch_json(url):
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


class Catalog:
    def __init__(self, version, items, champions):
        self.version = version
        self.items = {int(k): v for k, v in items.items()}
        # Live API gives display names ("Wukong"); match data gives ids ("MonkeyKing").
        self._champ_ids = {}
        for c in champions.values():
            self._champ_ids[_norm(c["name"])] = c["id"]
            self._champ_ids[_norm(c["id"])] = c["id"]

    @classmethod
    def load(cls, version=None):
        """Latest patch's data, or the newest cached copy if we're offline."""
        try:
            version = version or _fetch_json(f"{DDRAGON}/api/versions.json")[0]
        except requests.RequestException:
            cached = sorted(p.name for p in CACHE.glob("*") if (p / "item.json").exists())
            if not cached:
                raise
            version = cached[-1]
        folder = CACHE / version
        files = {}
        for name in ("item", "champion"):
            path = folder / f"{name}.json"
            if not path.exists():
                data = _fetch_json(f"{DDRAGON}/cdn/{version}/data/en_US/{name}.json")["data"]
                folder.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data), encoding="utf-8")
            files[name] = json.loads(path.read_text(encoding="utf-8"))
        return cls(version, files["item"], files["champion"])

    # --- lookups -------------------------------------------------------------
    def champion_id(self, name):
        return self._champ_ids.get(_norm(name or ""))

    def name(self, item_id):
        return self.items[item_id]["name"]

    def base(self, item_id):
        """Gold to combine this item from its components (its full price if it has none)."""
        return self.items[item_id]["gold"]["base"]

    def total(self, item_id):
        return self.items[item_id]["gold"]["total"]

    def components(self, item_id):
        return [int(c) for c in self.items[item_id].get("from", [])]

    def _on_rift(self, item_id):
        it = self.items.get(item_id)
        return bool(it) and it["maps"].get(SUMMONERS_RIFT) and it["gold"]["purchasable"]

    def is_boots(self, item_id):
        it = self.items.get(item_id)
        return bool(it) and "Boots" in it.get("tags", []) and it["gold"]["total"] >= BOOTS_MIN_GOLD

    def is_legendary(self, item_id):
        it = self.items.get(item_id)
        return (
            self._on_rift(item_id)
            and it["gold"]["total"] >= LEGENDARY_MIN_GOLD
            and "Boots" not in it.get("tags", [])
            and "Consumable" not in it.get("tags", [])
            and "Trinket" not in it.get("tags", [])
        )

    def is_trinket(self, item_id):
        return "Trinket" in self.items.get(item_id, {}).get("tags", [])

    def icon(self, item_id):
        """The item's 64x64 icon as a PIL image, downloaded once and cached."""
        path = CACHE / self.version / "icons" / f"{item_id}.png"
        if not path.exists():
            resp = requests.get(f"{DDRAGON}/cdn/{self.version}/img/item/{item_id}.png", timeout=30)
            resp.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(resp.content)
        return Image.open(path).convert("RGBA")

    # --- shopping ------------------------------------------------------------
    def remaining_cost(self, item_id, owned):
        """Gold still needed to hold `item_id`, spending pieces from `owned` (a Counter, used up in place)."""
        if owned[item_id] > 0:
            owned[item_id] -= 1
            return 0
        return self.base(item_id) + sum(self.remaining_cost(c, owned) for c in self.components(item_id))

    def _pieces(self, item_id, found=None):
        found = {} if found is None else found
        for c in self.components(item_id):
            found[c] = None
            self._pieces(c, found)
        return list(found)

    def next_step(self, target, owned_ids, gold):
        """What to buy now toward `target`: (kind, item_id, cost).

        kind is "complete" when the whole item is affordable, "component" for the biggest
        affordable piece of its recipe, or "save" when nothing useful is affordable yet
        (cost is then the gold still missing for the next piece).
        """
        need = self.remaining_cost(target, Counter(owned_ids))
        if need <= gold:
            return "complete", target, need
        best = None
        for piece in self._pieces(target):
            cost = self.remaining_cost(piece, Counter(owned_ids))
            if 0 < cost <= gold and (best is None or self.total(piece) > self.total(best[0])):
                best = (piece, cost)
        if best:
            return "component", best[0], best[1]
        cheapest = min(
            (self.remaining_cost(p, Counter(owned_ids)) for p in self._pieces(target)
             if self.remaining_cost(p, Counter(owned_ids)) > 0),
            default=need,
        )
        return "save", target, cheapest - gold


# --- picking the target from a build -----------------------------------------
def pick_entry(builds, champ_id, role):
    """The build entry for this champion and role, falling back to its most-played role."""
    roles = (builds or {}).get("champions", {}).get(champ_id)
    if not roles:
        return None
    return roles.get(role) or max(roles.values(), key=lambda r: r["games"])


def next_target(catalog, entry, owned_ids):
    """The next item from `entry` the player should be working toward, or None when the build is done.

    Works slot by slot: the player's n-th completed item fills slot n whatever it was, so a
    deviation from the build doesn't stall the recommendations. Boots go in at the point
    in the order the build's players usually bought them.
    """
    owned = set(owned_ids)
    legendaries = sum(1 for i in owned_ids if catalog.is_legendary(i))
    boots = entry.get("boots", {})
    if boots.get("items") and not any(catalog.is_boots(i) for i in owned) and legendaries >= boots["after"]:
        for c in boots["items"]:
            if c["id"] in catalog.items and c["id"] not in owned:
                return c["id"]
    for slot in range(legendaries, BUILD_SLOTS):
        for c in entry["slots"][slot]:
            if c["id"] in catalog.items and c["id"] not in owned:
                return c["id"]
    return None
