"""The overlay's next-item box: what to buy, when you can afford it, and the pixel art for it.

ItemAdvisor reads the live game data each second and says what to buy next from the build
data in data/builds.json (made by build_data.py). draw_item_box() shows just the item's
icon, pixelated, inside the gold frame in assets/item_frame.png.
"""

import json
from collections import Counter, deque
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image

from lol_items import Catalog, next_target, pick_entry

BUILDS_FILE = Path(__file__).with_name("data") / "builds.json"
FRAME_FILE = Path(__file__).with_name("assets") / "item_frame.png"

POP_COOLDOWN = 90  # seconds before "you can afford it" can pop up again for the same item
NO_POP_BEFORE = 90  # game seconds; "you can afford it" never pops before this
START_WINDOW = 90  # game seconds in which an empty inventory means "show the starting item"
PLACEHOLDER = (16, 30, 62)  # what fills the frame in drag mode when there's no item


@dataclass
class Rec:
    piece: int  # the finished item to work toward
    kind: str  # "start" | "next" (you just bought something) | "afford" (you can buy it now)
    name: str
    line2: str  # shown in the console when it pops
    icon: Image.Image = None  # pixelated, sized for the frame

    def key(self):
        return (self.piece, self.kind)


# --- the frame ------------------------------------------------------------------
@lru_cache(maxsize=1)
def _frame():
    """(frame image, opening box, icon size). The opening is the see-through middle, found by
    flood-filling from the centre, so the frame's outer corners and any notches in its inner
    corners are never mistaken for it."""
    frame = Image.open(FRAME_FILE).convert("RGBA")
    w, h = frame.size
    seen, queue = set(), deque([(w // 2, h // 2)])
    while queue:
        x, y = queue.popleft()
        if (x, y) in seen or not (0 <= x < w and 0 <= y < h) or frame.getpixel((x, y))[3] != 0:
            continue
        seen.add((x, y))
        queue.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    if not seen:
        raise RuntimeError(f"{FRAME_FILE.name} has no see-through middle for the item to sit in")
    xs, ys = [p[0] for p in seen], [p[1] for p in seen]
    box = (min(xs), min(ys), max(xs) + 1, max(ys) + 1)  # x0, y0, x1, y1 (exclusive)
    # The icon runs 1px past the opening on every side and goes under the frame, which
    # covers the overlap, so no gap shows and the frame's corner notches stay intact.
    icon = max(box[2] - box[0], box[3] - box[1]) + 2
    return frame, box, icon


def item_size():
    """Width and height of the item box in art pixels."""
    return _frame()[0].size


def pixel_icon(img, size=None):
    """Shrink a 64px icon to the art's pixel size and flatten its colours so it looks drawn, not scaled."""
    size = size or _frame()[2]
    small = img.convert("RGB").resize((size, size), Image.BOX)
    return small.quantize(colors=16, method=Image.Quantize.MEDIANCUT).convert("RGBA")


def draw_item_box(rec):
    """The framed item for `rec`; with None it's an empty frame, for drag mode."""
    frame, (x0, y0, x1, y1), icon_size = _frame()
    box = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    if rec and rec.icon is not None:
        box.paste(rec.icon, (x0 - 1, y0 - 1))
    else:
        box.paste(Image.new("RGBA", (x1 - x0, y1 - y0), PLACEHOLDER + (255,)), (x0, y0))
    box.alpha_composite(frame)  # the frame goes on top of the item, never under it
    return box


# --- advice ---------------------------------------------------------------------
def _me(data):
    ap = data["activePlayer"]
    names = {ap.get(k) for k in ("riotId", "riotIdGameName", "summonerName")} - {None, ""}
    for p in data["allPlayers"]:
        if names & {p.get(k) for k in ("riotId", "riotIdGameName", "summonerName")}:
            return p
    return None


class ItemAdvisor:
    def __init__(self):
        if not BUILDS_FILE.exists():
            raise RuntimeError(f"no {BUILDS_FILE.name}; run build_data.py first")
        _frame()  # fail now, with a clear message, if the frame art is missing or broken
        self.builds = json.loads(BUILDS_FILE.read_text(encoding="utf-8"))
        self.catalog = Catalog.load()
        self._icons = {}
        self._start_popped = False
        self._milestone = None  # how many build items you've bought, to spot a new purchase
        self._was_affordable = False
        self._last_target = None
        self._last_afford_pop = {}  # build target -> game time its "afford" popup last fired

    def _icon(self, piece):
        if piece not in self._icons:
            try:
                self._icons[piece] = pixel_icon(self.catalog.icon(piece))
            except Exception:  # offline with no cached icon: the frame just shows empty
                self._icons[piece] = None
        return self._icons[piece]

    def update(self, data):
        """(Rec or None, popup?) for the live game, or (None, False) if there's nothing to suggest."""
        me = _me(data)
        if not me:
            return None, False
        cat = self.catalog
        champ = cat.champion_id(me.get("championName"))
        entry = pick_entry(self.builds, champ, me.get("position"))
        if not entry:
            return None, False
        owned = [it["itemID"] for it in me.get("items", []) for _ in range(it.get("count", 1))]
        game_time = data["gameData"]["gameTime"]

        # Opening shop: nothing bought yet (the free trinket doesn't count), so suggest the starter.
        if game_time <= START_WINDOW and not any(not cat.is_trinket(i) for i in owned):
            combo = [i for i in (entry.get("start") or [{"items": []}])[0]["items"] if i in cat.items]
            if combo:
                piece = max(combo, key=cat.total)  # the starting item itself, not the potions bought with it
                pop = not self._start_popped
                self._start_popped = True
                self._milestone = 0  # so buying the starter counts as the first step
                return Rec(piece, "start", cat.name(piece), "Starting item", self._icon(piece)), pop

        # Each purchase of the starter, boots or a finished item moves the build along one step.
        milestone = sum(1 for i in owned if cat.is_legendary(i) or cat.is_boots(i))
        milestone += any(not cat.is_trinket(i) for i in owned)
        bought_something, self._milestone = (
            self._milestone is not None and milestone > self._milestone, milestone)

        target = next_target(cat, entry, owned)
        if target is None:
            return None, False
        gold = data["activePlayer"].get("currentGold", 0)
        affordable = cat.remaining_cost(target, Counter(owned)) <= gold

        # Gold hovering around the price (a potion, a ward) can't re-trigger it inside the cooldown.
        newly_affordable = (
            affordable and game_time >= NO_POP_BEFORE
            and (not self._was_affordable or target != self._last_target)
            and game_time - self._last_afford_pop.get(target, -1e9) >= POP_COOLDOWN
        )
        self._was_affordable, self._last_target = affordable, target
        if newly_affordable:
            self._last_afford_pop[target] = game_time

        kind, line2 = ("afford", "Buy now") if newly_affordable else ("next", "Next in your build")
        rec = Rec(target, kind, cat.name(target), line2, self._icon(target))
        return rec, newly_affordable or bought_something


def demo_rec():
    """A made-up 'buy Infinity Edge' suggestion for previewing the box without a game."""
    cat = Catalog.load()
    return Rec(3031, "afford", cat.name(3031), "Buy now", pixel_icon(cat.icon(3031)))
