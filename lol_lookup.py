"""Champion lookup window for champion select: runes and counters.

It is an ordinary always-on-top window (so it can take typing and be dragged anywhere on the
screen), opened by the overlay when champion select starts and closed when it ends. The art is
assets/lookup_panel.png; its see-through holes are where the pixelated icons go. The numbers
come from data/builds.json.
"""

import re
import tkinter as tk
from functools import lru_cache

import requests
from PIL import Image, ImageDraw, ImageFont, ImageTk

from lol_itembox import pixel_icon
from lol_items import CACHE, DDRAGON
from lol_runes import load_icon_paths, load_tree_names
from paths import RESOURCE_DIR

PANEL_FILE = RESOURCE_DIR / "assets" / "lookup_panel.png"
FONT_FILE = RESOURCE_DIR / "assets" / "Romulus.ttf"
TITLE_FONT_FILE = RESOURCE_DIR / "assets" / "Jacquard12-Regular.ttf"  # pixel blackletter, built on a 12px grid
SCALE = 1.5  # screen pixels per art pixel (2 is the art at double size)
# The art's text sizes (16, 32) drawn at whole multiples of Romulus's 8px grid for this scale.
FONT_PX = {16: 16, 32: round(32 * SCALE / 2 / 8) * 8, 48: round(48 * SCALE / 2 / 8 + 0.01) * 8}

MIN_SHOWN = 50 # fewer lane games than this against a champion and it isn't listed (a few lucky games aren't a counter)
LOW_CONFIDENCE = 50  # below this many games a matchup gets a * (still a thin sample)
PRIOR = 10  # games' worth of the champion's overall win rate mixed into each matchup
TOP = 3

# Where things sit on the art (art pixels).
SEARCH_SLOT = (32, 32, 198, 47)  # x0, y0, x1, y1 of the dark search slot
PORTRAIT = (32, 64, 166, 163)  # x, y, width, height of the champion's hole
KEYSTONE = (219, 42, 37)
PRIMARY_MINORS = [(225, 98, 25), (225, 145, 25), (225, 192, 25)]  # under the keystone
SECONDARY_MINORS = [(283, 98, 25), (283, 145, 25)]
STRIP_X = (32, 175, 318)  # the three icon holes of the counters strip, 30px
STRIP_Y = 294
STRIP_ICON = 30
STRIP_TEXT_W = (95, 95, 89)  # room for text to the right of each icon
# The three boxes down the right: x0, y0, x1, y1.
TITLE_BOX = (324, 7, 450, 104)  # "Champion Search": from the top of the gem down to the champion name; same centre line (x 387)
TITLE_DROP = 6  # art pixels the title is nudged down from dead centre
TITLE_GAP = 20  # art pixels of space wanted above and below the title: the gap between the stats and the rune stats
NAME_BOX = (324, 95, 450, 158)  # champion name, games, win rate
INFO_BOX = (324, 162, 450, 234)  # runes and everything else
ROLE_ARROWS = ((274.5, 204), (315.5, 204))  # centres of the role toggle's arrows, drawn in the art
ROLE_WORD = (295, 204)  # midpoint of the two arrows
NOTE_RIGHT, NOTE_Y = 442, 245  # right edge of the footnote; its centre line is the middle of the gem's bottom face
CLOSE_AT = (437, 14)  # centre of the X that closes the window, top right
HEADINGS = [(33, 276, "Counters")]  # (x, centre y, text), just right of the crosshair's vertical dash (x 21)
HIT = 8  # art pixels around an arrow that count as clicking it

ROLE_NAMES = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "Bot", "UTILITY": "Support"}
FAINT = 0.8  # opacity of the X and the footnote
CLOSE_RED = (255, 90, 90)  # the X that closes the window
WHITE, SHADOW = (255, 255, 255), (8, 14, 40)
DIM = WHITE  # every text is white; kept as a name so secondary lines are easy to restyle
SPLASH_ZOOM, SPLASH_FOCUS_Y = 1.4, 0.42  # how far the portrait is zoomed in, and how high on the art it centres
KEY = (1, 2, 3)  # the see-through colour: the gaps between the gems and the corners
EMPTY_HOLE = (8, 18, 38, 255)  # what an icon-less hole shows


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def matchup_lists(entry):
    """(counters, good) for a champion-role entry: up to 3 each of (opponent id, games, win rate).

    Ranking uses the searched champion's win rate pulled toward its overall one, so a lucky few
    games can't top a list. The rate shown is the raw one, always the searched champion's win rate
    against that opponent: low for counters, high for good matchups."""
    base = entry["wr"]
    rows = []
    for opp, (n, wins) in entry.get("vs", {}).items():
        if n >= MIN_SHOWN:
            rows.append((opp, n, wins / n, (wins + PRIOR * base) / (n + PRIOR)))
    good = sorted((r for r in rows if r[3] > base), key=lambda r: -r[3])[:TOP]
    counters = sorted((r for r in rows if r[3] < base), key=lambda r: r[3])[:TOP]
    return ([(o, n, wr) for o, n, wr, _ in counters], [(o, n, wr) for o, n, wr, _ in good])


def _cached_image(url, path):
    """A downloaded image, kept on disk; None if it can't be had (offline, bad id)."""
    try:
        if not path.exists():
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(resp.content)
        return Image.open(path).convert("RGBA")
    except (OSError, requests.RequestException):
        return None


def _pixelate(img, w, h, colors=24, zoom=1.0, focus_y=0.5):
    """`img` cropped to fill w x h (zoomed in, centred across and `focus_y` of the way down), shrunk
    and flattened to a few colours so it looks drawn."""
    iw, ih = img.size
    cw, ch = (round(ih * w / h), ih) if iw * h > ih * w else (iw, round(iw * h / w))
    cw, ch = round(cw / zoom), round(ch / zoom)
    left, top = (iw - cw) // 2, min(max(round(focus_y * ih - ch / 2), 0), ih - ch)
    small = img.convert("RGB").crop((left, top, left + cw, top + ch)).resize(
        (w, h), Image.BOX if cw >= w else Image.BICUBIC)
    return small.quantize(colors=colors, method=Image.Quantize.MEDIANCUT).convert("RGBA")


@lru_cache(maxsize=1)
def _panel():
    return Image.open(PANEL_FILE).convert("RGBA")


class Lookup:
    def __init__(self, root, builds, display_names, version, on_close):
        self.builds = builds["champions"] if builds else {}
        self.display = {cid: display_names.get(cid, cid) for cid in self.builds}
        self.version = version
        self.rune_paths = load_icon_paths(version)
        self.tree_names = load_tree_names(version)
        self._title = None
        self.on_close = on_close
        self.champ = self.role = None
        self.roles = []
        self._shown = []
        self._icons = {}  # (kind, id, size) -> pixelated icon, or None
        self.fonts = {size: ImageFont.truetype(str(FONT_FILE), px) for size, px in FONT_PX.items()}  # the gem's font
        self.message = "Type a champion to look it up." if self.builds else "No data yet; run build_data.py."

        win = self.win = tk.Toplevel(root)
        win.title("Champion lookup")
        win.overrideredirect(True)  # no title bar: the art is the window; drag it by the art
        win.attributes("-topmost", True)
        win.attributes("-transparentcolor", "#%02x%02x%02x" % KEY)
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.resizable(False, False)

        w, h = _panel().size
        self.canvas = tk.Canvas(win, width=round(w * SCALE), height=round(h * SCALE), highlightthickness=0, bd=0,
                                bg="#%02x%02x%02x" % KEY)
        self.canvas.pack()
        self.picture = self.canvas.create_image(0, 0, anchor="nw")
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<B1-Motion>", self._drag)
        self._grab = None

        x0, y0, x1, y1 = SEARCH_SLOT
        self.entry_font = self._typing_font()
        self.query = tk.StringVar()
        self.query.trace_add("write", lambda *_: self._search())
        self.entry = tk.Entry(self.canvas, textvariable=self.query, bd=0, highlightthickness=0,
                              bg="#081226", fg="white", insertbackground="white", font=self.entry_font)
        self.entry.bind("<Return>", lambda e: self._pick_first())
        self.canvas.create_window(round(x0 * SCALE), round(y0 * SCALE), anchor="nw", window=self.entry,
                                  width=round((x1 - x0) * SCALE), height=round((y1 - y0) * SCALE))
        self.matches = tk.Listbox(self.canvas, height=5, exportselection=False, bd=0, highlightthickness=0,
                                  bg="#081226", fg="white", selectbackground="#2e6ae5", font=self.entry_font)
        self.matches.bind("<<ListboxSelect>>", lambda e: self._pick_selected())
        self.match_box = self.canvas.create_window(round(x0 * SCALE), round(y1 * SCALE) + 2, anchor="nw", window=self.matches,
                                                   width=round((x1 - x0) * SCALE), state="hidden")
        self._render()
        win.after(100, self.entry.focus_force)  # a borderless window has to ask for typing focus

    def _typing_font(self):
        """Romulus for the search box too, loaded for this process only; Consolas if that fails."""
        try:
            import ctypes
            if ctypes.windll.gdi32.AddFontResourceExW(str(FONT_FILE), 0x10, 0):  # 0x10: private to this process
                return ("Romulus", -FONT_PX[32])  # negative: size in pixels
        except (AttributeError, OSError):
            pass
        return ("Consolas", 12)

    # --- search -------------------------------------------------------------------
    def _matching(self, text):
        q = _norm(text)
        if not q:
            return []
        found = [(0 if _norm(n).startswith(q) else 1, n, cid) for cid, n in self.display.items() if q in _norm(n)]
        return [(n, cid) for _, n, cid in sorted(found)][:8]

    def _search(self):
        self._shown = self._matching(self.query.get())
        self.matches.delete(0, "end")
        for name, _ in self._shown:
            self.matches.insert("end", name)
        self.matches.config(height=max(1, len(self._shown)))
        done = len(self._shown) == 1 and self._shown[0][0] == self.query.get()
        self.canvas.itemconfigure(self.match_box, state="normal" if self._shown and not done else "hidden")

    def _pick_first(self):
        if self._shown:
            self.choose(self._shown[0][1])

    def _pick_selected(self):
        sel = self.matches.curselection()
        if sel:
            self.choose(self._shown[sel[0]][1])

    def choose(self, champ_id, role=None):
        roles = sorted(self.builds.get(champ_id, {}), key=lambda r: -self.builds[champ_id][r]["games"])
        self.champ = champ_id
        self.roles = roles
        self.role = role if role in roles else (roles[0] if roles else None)
        self.query.set(self.display.get(champ_id, champ_id))
        self.canvas.itemconfigure(self.match_box, state="hidden")
        self._render()

    def _step(self, direction):
        """The arrows beside the role name: previous / next role this champion has data for."""
        if len(self.roles) > 1:
            self.choose(self.champ, self.roles[(self.roles.index(self.role) + direction) % len(self.roles)])

    def _click(self, event):
        x, y = event.x / SCALE, event.y / SCALE
        self._grab = None
        if abs(x - CLOSE_AT[0]) <= HIT and abs(y - CLOSE_AT[1]) <= HIT:
            return self.close()
        for direction, (ax, ay) in zip((-1, 1), ROLE_ARROWS):
            if abs(x - ax) <= HIT and abs(y - ay) <= HIT:
                return self._step(direction)
        self._grab = (event.x_root - self.win.winfo_x(), event.y_root - self.win.winfo_y())

    def _drag(self, event):
        if self._grab:
            self.win.geometry(f"+{event.x_root - self._grab[0]}+{event.y_root - self._grab[1]}")

    # --- icons --------------------------------------------------------------------
    def _icon(self, kind, key, size):
        """A pixelated icon sized to fit a hole of `size` (it runs 1px under the art on each side)."""
        slot = (kind, key, size)
        if slot not in self._icons:
            if kind == "champ":
                src = _cached_image(f"{DDRAGON}/cdn/{self.version}/img/champion/{key}.png",
                                    CACHE / self.version / "champ_icons" / f"{key}.png")
            else:
                path = self.rune_paths.get(key)
                src = path and _cached_image(f"{DDRAGON}/cdn/img/{path}", CACHE / "rune_icons" / f"{key}.png")
            if src:  # rune art is see-through, with stray colour hidden under the clear pixels: lay it on the hole's dark first
                backing = Image.new("RGBA", src.size, EMPTY_HOLE)
                backing.alpha_composite(src)
                src = backing
            self._icons[slot] = pixel_icon(src, size + 2) if src else None
        return self._icons[slot]

    # --- drawing ------------------------------------------------------------------
    def _portrait(self, champ, w, h):
        """The champion's splash art (centred on them) pixelated to fill the big hole; their icon if offline."""
        slot = ("portrait", champ, (w, h))
        if slot not in self._icons:
            src = _cached_image(f"{DDRAGON}/cdn/img/champion/centered/{champ}_0.jpg",
                                CACHE / "splash" / f"{champ}.jpg")
            if src:
                self._icons[slot] = _pixelate(src, w + 2, h + 2, zoom=SPLASH_ZOOM, focus_y=SPLASH_FOCUS_Y)
            else:  # offline: the small icon, whole
                icon = _cached_image(f"{DDRAGON}/cdn/{self.version}/img/champion/{champ}.png",
                                     CACHE / self.version / "champ_icons" / f"{champ}.png")
                self._icons[slot] = _pixelate(icon, w + 2, h + 2) if icon else None
        return self._icons[slot]

    def _render(self):
        entry = self.builds.get(self.champ, {}).get(self.role)
        under = Image.new("RGBA", _panel().size, (0, 0, 0, 0))
        texts = []  # (art x, art y, text, colour, size)

        def hole(x, y, w, h, icon):
            if icon is not None:
                under.paste(icon, (x - 1, y - 1))
            else:
                under.paste(Image.new("RGBA", (w, h), EMPTY_HOLE), (x, y))

        px, py, pw, ph = PORTRAIT
        hole(px, py, pw, ph, self._portrait(self.champ, pw, ph) if entry else None)
        counters = matchup_lists(entry)[0] if entry else []
        page = (entry.get("runes") or [None])[0] if entry else None
        slots = [KEYSTONE] + PRIMARY_MINORS + SECONDARY_MINORS
        ids = (page["keys"] + page["minor"]) if page else [None] * len(slots)
        for (x, y, size), rune in zip(slots, ids):
            hole(x, y, size, size, self._icon("rune", rune, size) if rune else None)
        for i, x in enumerate(STRIP_X):
            if i < len(counters):
                opp, n, wr = counters[i]
                hole(x, STRIP_Y, STRIP_ICON, STRIP_ICON, self._icon("champ", opp, STRIP_ICON))
                star = "*" if n < LOW_CONFIDENCE else ""
                texts.append(self._text(x + 38, STRIP_Y, self.display.get(opp, opp), WHITE, STRIP_TEXT_W[i]))
                texts.append(self._text(x + 38, STRIP_Y + 14, f"{wr:.0%} win ({n}){star}", DIM, STRIP_TEXT_W[i]))
            else:
                hole(x, STRIP_Y, STRIP_ICON, STRIP_ICON, None)
                if i == 0 and entry:
                    texts.append(self._text(x + 38, STRIP_Y + 7, "not enough games", DIM, STRIP_TEXT_W[i]))

        nx0, ny0, nx1, ny1 = NAME_BOX
        ix0, iy0, ix1, iy1 = INFO_BOX
        ncx, icx = (nx0 + nx1) / 2, (ix0 + ix1) / 2
        width = nx1 - nx0 - 4
        if entry:  # green box: who, how many games, how often they win
            texts.append(self._centred_fit(ncx, ny0 + 15, self.display.get(self.champ, self.champ), WHITE, width, 48))
            texts.append(self._centred_fit(ncx, ny0 + 37, f"{entry['games']} games", DIM, width))
            texts.append(self._centred_fit(ncx, ny0 + 52, f"{entry['wr']:.0%} win", DIM, width))
            # yellow box: the rune page's pick and win rate, and which two trees it is
            if page:
                texts.append(self._centred_fit(icx, iy0 + 11, f"Runes: {page['share']:.0%} pick", WHITE, width))
                texts.append(self._centred_fit(icx, iy0 + 29, f"{page['wr']:.0%} win", DIM, width))
                trees = f"{self.tree_names.get(page['primary'], '?')} + {self.tree_names.get(page['sub'], '?')}"
                texts.append(self._centred_fit(icx, iy0 + 51, trees, DIM, width, 16))
            else:
                texts.append(self._centred_fit(icx, iy0 + 29, "No rune data yet", DIM, width))
        else:
            message = "No data for that champion yet." if self.champ else self.message
            lines, line = [], ""
            for word in message.split():  # wrap to the box
                if line and self.fonts[16].getlength(f"{line} {word}") > width * SCALE:
                    lines.append(line)
                    line = ""
                line = f"{line} {word}".strip()
            lines.append(line)
            for i, line in enumerate(lines):
                texts.append(self._centred_fit(ncx, (ny0 + ny1) / 2 + (i - (len(lines) - 1) / 2) * 11, line, DIM, width, 16))
        if self.role:  # the toggle: the arrows are in the art, the role goes between them
            texts.append(self._centred(ROLE_WORD[0], ROLE_WORD[1], ROLE_NAMES.get(self.role, self.role.title()), WHITE, 16))
            texts.append(self._centred(ROLE_WORD[0], ROLE_WORD[1] - 18, "Role", DIM, 16))

        for x, cy, label in HEADINGS:  # what each section is
            t, b = self.fonts[32].getbbox(label)[1::2]
            texts.append((x, cy - (t + b) / 2 / SCALE, label, WHITE, 32))
        faint = [self._centred(CLOSE_AT[0], CLOSE_AT[1], "X", CLOSE_RED, 16)]  # closes the window
        if entry:  # the footnote: bottom right, centred up and down in the bottom face
            note = f"* = under {LOW_CONFIDENCE} games"
            l, t, r, b = self.fonts[16].getbbox(note)
            faint.append((NOTE_RIGHT - r / SCALE, NOTE_Y - (t + b) / 2 / SCALE, note, DIM, 16))

        art = Image.new("RGBA", under.size, KEY + (255,))
        art.alpha_composite(under)
        art.alpha_composite(_panel())  # the art goes over the icons, covering their 1px overlap
        big = art.resize((round(art.width * SCALE), round(art.height * SCALE)), Image.NEAREST)
        font, lines = self._title_layout()
        cx = (TITLE_BOX[0] + TITLE_BOX[2]) / 2 * SCALE
        pixel = ImageDraw.Draw(big)
        pixel.fontmode = "1"  # no anti-aliasing: the title's pixels stay hard-edged
        for line, baseline in lines:
            pixel.text((cx + 2, baseline + 2), line, font=font, fill=SHADOW, anchor="ms")
            pixel.text((cx, baseline), line, font=font, fill=WHITE, anchor="ms")
        draw = ImageDraw.Draw(big)
        for x, y, text, colour, size in texts:
            draw.text((x * SCALE + 2, y * SCALE + 2), text, font=self.fonts[size], fill=SHADOW)
            draw.text((x * SCALE, y * SCALE), text, font=self.fonts[size], fill=colour)
        overlay = Image.new("RGBA", big.size, (0, 0, 0, 0))  # these two are drawn see-through, over the art
        fade = ImageDraw.Draw(overlay)
        alpha = round(255 * FAINT)
        for x, y, text, colour, size in faint:
            fade.text((x * SCALE + 2, y * SCALE + 2), text, font=self.fonts[size], fill=SHADOW + (alpha,))
            fade.text((x * SCALE, y * SCALE), text, font=self.fonts[size], fill=colour + (alpha,))
        big.alpha_composite(overlay)
        self._photo = ImageTk.PhotoImage(big)
        self.canvas.itemconfigure(self.picture, image=self._photo)

    def _title_layout(self):
        """(font, [(line, baseline y)]) for the title: one or two lines in the biggest size, a whole multiple
        of the font's 12px grid, that leaves the space above and below it nearest TITLE_GAP, centred in the
        title box by the drawn letters rather than the font's line box."""
        if self._title is None:
            tx0, ty0, tx1, ty1 = TITLE_BOX
            box_w, box_h = (tx1 - tx0 - 4) * SCALE, (ty1 - ty0) * SCALE
            best = None
            for px in range(12, 240, 12):
                font = ImageFont.truetype(str(TITLE_FONT_FILE), px)
                for lines in (("Champion Search",), ("Champion", "Search")):
                    ink = [font.getbbox(t, anchor="ls")[1::2] for t in lines]  # (top, bottom) around each baseline
                    between = px * 0.1
                    height = sum(b - t for t, b in ink) + between * (len(lines) - 1)
                    if max(font.getlength(t) for t in lines) > box_w or height > box_h:
                        continue
                    off = abs((box_h - height) / 2 - TITLE_GAP * SCALE)
                    if best is None or off <= best[0]:
                        y = ty0 * SCALE + (box_h - height) / 2 + TITLE_DROP * SCALE
                        placed = []
                        for t, (top, bottom) in zip(lines, ink):
                            placed.append((t, y - top))
                            y += bottom - top + between
                        best = (off, font, placed)
            self._title = best[1:]
        return self._title

    def _fit(self, text, width, size=32):
        """(text, font size) for `text` in `width` art pixels: the big size if it fits, else the
        small one, else the small one cut short."""
        for size in [z for z in (48, 32, 16) if z <= size]:
            if self.fonts[size].getlength(text) <= width * SCALE:
                return text, size
        while len(text) > 1 and self.fonts[size].getlength(text) > width * SCALE:
            text = text[:-1]
        return text, size

    def _centred(self, cx, cy, text, colour, size):
        """A text tuple whose drawn letters (not the font's line box) are centred on (cx, cy)."""
        l, t, r, b = self.fonts[size].getbbox(text)
        return cx - (l + r) / 2 / SCALE, cy - (t + b) / 2 / SCALE, text, colour, size

    def _centred_fit(self, cx, cy, text, colour, width, size=32):
        """_centred, at the biggest size up to `size` that fits `width` art pixels."""
        text, size = self._fit(text, width, size)
        return self._centred(cx, cy, text, colour, size)

    def _text(self, x, y, text, colour, width, size=32):
        text, size = self._fit(text, width, size)
        return x, y, text, colour, size

    def lift(self):
        self.win.lift()
        self.win.attributes("-topmost", True)

    def close(self):
        try:
            self.on_close(self.win.winfo_x(), self.win.winfo_y())
            self.win.destroy()
        except tk.TclError:
            pass

    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False
