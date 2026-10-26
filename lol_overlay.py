"""Always-on-top, click-through win-chance overlay for a live League game.

    py lol_overlay.py --rank GOLD [--x 20 --y 200]

Run it before or during a game. It reuses the polling, snapshot and model code from
lol_snapshots.py and shows the win chance as a pixel-art gem over the game instead of
printing it. Mouse clicks pass straight through it to the game. The art and fonts live
in assets/ (needs Pillow: py -m pip install pillow).

League must be in Borderless or Windowed mode; overlays can't draw over exclusive
fullscreen.

The box is hidden except for 45 seconds after the 10:00 and 15:00 reads.

Hotkeys (work while the game has focus):
    Ctrl+Alt+D  toggle drag mode (box shows and becomes clickable so you can move it)
    Ctrl+Alt+Q  quit (saves captured rows first)

Snapshots are still saved to lol_snapshots.csv when the game ends or you quit.
"""

import argparse
import ctypes
import json
import threading
import time
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageTk

import lol_snapshots as ls

# Win32 constants
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
WS_EX_LAYERED = 0x80000
WS_EX_TOOLWINDOW = 0x80
WS_EX_NOACTIVATE = 0x08000000
VK_CONTROL, VK_MENU, VK_D, VK_Q = 0x11, 0x12, 0x44, 0x51

ASSETS = Path(__file__).with_name("assets")
SPRITE_FILE = ASSETS / "gem.png"  # 128x64 pixel art, transparent corners
FONT_FILE = ASSETS / "Romulus.ttf"  # 16px bitmap font; 16 and 32 are whole multiples of its grid
SCALE = 2  # on-screen pixels per art pixel
KEY = (1, 2, 3)  # window colour that Windows renders as see-through

# Everything below is in art pixels (the sprite's own 128x64 grid).
# The gem's middle face spans x 14-114, y 14-50.
MAIN_POS = (41, 26)  # centre of the big number, left half of the face
CAPTION_X, CAPTION_Y = 89, (21, 32)  # centre column and the two caption rows, right half
BAR_BOX = (24, 41, 104, 47)  # x0, y0, x1, y1 (exclusive) of the bar's outer border
WHITE, SHADOW = (255, 255, 255), (0, 45, 22)
BAR_DARK = (0, 55, 26)
BLUE_PX = {"hi": (150, 205, 255), "mid": (66, 140, 235), "lo": (36, 88, 175)}
RED_PX = {"hi": (255, 150, 135), "mid": (222, 72, 66), "lo": (150, 40, 46)}
SHOW_SECONDS = 45  # how long the box stays up after each read
ALPHA = 1.0  # the art is opaque; lower this to let the game show through


class State:
    """Shared between the polling thread and the Tk thread."""

    def __init__(self):
        self.lock = threading.Lock()
        self.status = "Waiting for a live game..."
        self.game_time = None
        self.blue = None  # latest blue win chance
        self.minute = None  # minute that chance was computed at
        self.delta = None  # my-team change vs the earlier read, in points
        self.read_id = 0  # bumped on every new read so the overlay knows to pop up
        self.my_side = None  # "blue" / "red" / None
        self.done = False
        self.stop = threading.Event()


def poll(state, rank):
    """Same loop as lol_snapshots.main(), publishing into `state` instead of printing."""
    models = ls.load_models()
    print(f"Loaded models for minute(s): {sorted(models) or 'none'}")
    match_id = time.strftime("LIVE_%Y%m%d_%H%M%S")
    pending, rows, result, all_events, earlier = list(ls.MARKS), [], None, [], None
    in_game = False
    print("Waiting for a live game (start one; League must be in-game, not in lobby/loading)...")
    try:
        while not state.stop.is_set():
            data = ls.fetch(quiet=True)
            if data is None:
                if rows:
                    break
                with state.lock:
                    state.status, state.game_time = "Waiting for a live game...", None
                state.stop.wait(5)
                continue
            if not in_game:
                in_game = True
                print("Game detected - tracking. Reads at 10:00 and 15:00.")
            all_events = data["events"]["Events"]
            game_time = data["gameData"]["gameTime"]
            team = ls.my_team(data)
            with state.lock:
                state.game_time = game_time
                state.my_side = {"ORDER": "blue", "CHAOS": "red"}.get(team)
                if state.blue is None:
                    state.status = "Next read at 10:00"
            if pending and game_time >= pending[0] * 60:
                minute = pending.pop(0)
                rows.append(ls.snapshot(data, minute, match_id, rank))
                print(f"Captured {minute}-minute snapshot (game time {game_time:.0f}s)")
                if minute not in models:
                    print(f"  No model for minute {minute}; nothing to show.")
                if minute in models and game_time - minute * 60 <= ls.LATE_SECONDS:
                    blue = ls.blue_win_chance(rows[-1], models[minute])
                    print(f"  Blue {blue:.0%} / red {1 - blue:.0%} - overlay popping up")
                    delta = None
                    if earlier and team:
                        mine, was = (blue, earlier[1]) if team == "ORDER" else (1 - blue, 1 - earlier[1])
                        delta = (mine - was) * 100
                    earlier = (minute, blue)
                    with state.lock:
                        state.blue, state.minute, state.delta = blue, minute, delta
                        state.read_id += 1
                elif minute in models:
                    with state.lock:
                        state.status = f"{minute}:00 read missed (joined late)"
            if pending:
                with state.lock:
                    if state.blue is not None:
                        state.status = f"Next read at {pending[0]}:00"
            elif not pending:
                with state.lock:
                    state.status = "Reads done" if state.blue is not None else "No read this game"
            result = ls.blue_won(data)
            if result is not None:
                break
            state.stop.wait(1 if pending else 5)
    finally:
        for r in rows:
            r["blue_win"] = result
        if rows:
            ls.write_rows(rows)
            print(f"Saved {len(rows)} row(s) to {ls.OUT_FILE}")
        else:
            print("No snapshots captured.")
        if all_events:
            events_file = ls.OUT_FILE.with_name(f"{match_id}_events.json")
            events_file.write_text(json.dumps(all_events, indent=1), encoding="utf-8")
        with state.lock:
            state.done = True
            state.status = "Game over - saved" if rows else "Game over"


class Overlay:
    def __init__(self, state, x, y):
        self.state = state
        self.user32 = ctypes.windll.user32
        self.drag_mode = False
        self.hotkey_held = {"d": False, "q": False}

        root = self.root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True, "-alpha", 0.0)  # hidden until a read arrives
        self.seen_read = 0
        self.show_until = 0.0
        self.visible = False
        self.sprite = Image.open(SPRITE_FILE).convert("RGBA")
        self.font_big = ImageFont.truetype(str(FONT_FILE), 32)
        self.font_small = ImageFont.truetype(str(FONT_FILE), 16)
        self.drawn = None  # (main, sub, bar split) currently on screen
        w, h = self.sprite.width * SCALE, self.sprite.height * SCALE
        key_hex = "#%02x%02x%02x" % KEY
        root.geometry(f"{w}x{h}+{x}+{y}")
        root.configure(bg=key_hex)
        root.attributes("-transparentcolor", key_hex)

        # One label shows the whole pre-rendered image; dragging works while drag mode is on.
        self.panel = tk.Label(root, bg=key_hex, bd=0, highlightthickness=0)
        self.panel.pack()
        for widget in (root, self.panel):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)

        root.update_idletasks()
        self.hwnd = self.user32.GetParent(root.winfo_id()) or root.winfo_id()
        self._set_click_through(True)
        self._tick()

    # --- click-through -----------------------------------------------------
    def _set_click_through(self, on):
        style = self.user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        style = style | WS_EX_TRANSPARENT if on else style & ~WS_EX_TRANSPARENT
        self.user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style)

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    # --- hotkeys -----------------------------------------------------------
    def _down(self, vk):
        return bool(self.user32.GetAsyncKeyState(vk) & 0x8000)

    def _check_hotkeys(self):
        combo = self._down(VK_CONTROL) and self._down(VK_MENU)
        for name, vk in (("d", VK_D), ("q", VK_Q)):
            pressed = combo and self._down(vk)
            if pressed and not self.hotkey_held[name]:
                if name == "d":
                    self.drag_mode = not self.drag_mode
                    self._set_click_through(not self.drag_mode)
                else:
                    self.quit()
            self.hotkey_held[name] = pressed

    def quit(self):
        self.state.stop.set()
        self.root.after(300, self.root.destroy)

    # --- rendering ---------------------------------------------------------
    def _tick(self):
        self._check_hotkeys()
        with self.state.lock:
            s = self.state
            blue, minute = s.blue, s.minute
            delta, side, read_id = s.delta, s.my_side, s.read_id
        if read_id != self.seen_read:
            self.seen_read = read_id
            self.show_until = time.monotonic() + SHOW_SECONDS
        show = self.drag_mode or time.monotonic() < self.show_until
        if show != self.visible:
            self.visible = show
            self.root.attributes("-alpha", ALPHA if show else 0.0)
        if blue is None:
            main, lines, frac = "--", ("Drag", "me"), 0.5  # only visible in drag mode
        else:
            mine = blue if side != "red" else 1 - blue
            main, frac = f"{mine:.0%}", blue
            lines = ((side or "blue").capitalize() + (f"{delta:+.0f}" if delta is not None else ""),
                     f"{minute}:00")
        self._draw(main, lines, frac)
        self.root.after(250, self._tick)

    def _draw(self, main, lines, blue_frac):
        """Render onto the sprite at its native resolution, then scale up without smoothing."""
        x0, y0, x1, y1 = BAR_BOX
        split = round((x1 - x0 - 2) * blue_frac)
        if self.drawn == (main, lines, split):
            return
        self.drawn = (main, lines, split)

        art = self.sprite.copy()
        d = ImageDraw.Draw(art)
        d.fontmode = "1"  # no anti-aliasing, so glyphs stay on the pixel grid
        texts = [(main, self.font_big, MAIN_POS)]
        texts += [(t, self.font_small, (CAPTION_X, y)) for t, y in zip(lines, CAPTION_Y)]
        for text, font, (cx, cy) in texts:
            d.text((cx + 1, cy + 1), text, font=font, fill=SHADOW, anchor="mm")
            d.text((cx, cy), text, font=font, fill=WHITE, anchor="mm")

        d.rectangle((x0, y0, x1 - 1, y1 - 1), fill=BAR_DARK)  # border and empty-bar backing
        ix0, iy0, ix1 = x0 + 1, y0 + 1, x1 - 1  # inner area: 4 rows tall
        for part, (lo, hi) in (("blue", (ix0, ix0 + split)), ("red", (ix0 + split, ix1))):
            px = BLUE_PX if part == "blue" else RED_PX
            if hi > lo:
                d.rectangle((lo, iy0, hi - 1, iy0), fill=px["hi"])
                d.rectangle((lo, iy0 + 1, hi - 1, iy0 + 2), fill=px["mid"])
                d.rectangle((lo, iy0 + 3, hi - 1, iy0 + 3), fill=px["lo"])
        mid = (ix0 + ix1) // 2
        d.rectangle((mid, iy0, mid, iy0 + 3), fill=BAR_DARK)  # 50% tick

        flat = Image.new("RGB", art.size, KEY)
        flat.paste(art, mask=art.getchannel("A"))
        flat = flat.resize((art.width * SCALE, art.height * SCALE), Image.NEAREST)
        self.photo = ImageTk.PhotoImage(flat)  # keep a reference or Tk drops it
        self.panel.config(image=self.photo)

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rank", default="", help="rank_tier label, e.g. GOLD")
    parser.add_argument("--x", type=int, default=20)
    parser.add_argument("--y", type=int, default=200)
    parser.add_argument("--demo", nargs="?", type=int, const=10, choices=ls.MARKS, metavar="MINUTE",
                        help="show the box now with fake data (no game needed); "
                             "--demo 10 or --demo 15 picks which read to preview")
    args = parser.parse_args()

    state = State()
    if args.demo:
        state.minute, state.blue, state.my_side, state.read_id = args.demo, 0.62, "blue", 1
        state.delta = 4.0 if args.demo == max(ls.MARKS) else None  # only the later read has a change
        print(f"Demo mode ({args.demo}:00 read): box shows top-left for {SHOW_SECONDS}s. Ctrl+Alt+Q quits.")
        worker = threading.Thread(target=lambda: None)
    else:
        worker = threading.Thread(target=poll, args=(state, args.rank.upper()), daemon=False)
    worker.start()
    overlay = Overlay(state, args.x, args.y)
    try:
        overlay.run()
    finally:
        state.stop.set()
        worker.join(timeout=10)  # let it save rows


if __name__ == "__main__":
    main()
