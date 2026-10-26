# LoL live win predictor

Reads a live League of Legends game and estimates each team's win chance at the 10 and 15
minute marks. It can print the result in a terminal or show it as a pixel-art overlay on
top of the game.

The model is a logistic regression trained on 8,167 matches. It only uses things any player
can see in game: level, CS, kills, assists, towers, dragons, heralds, void grubs and first
blood, as team differences. Gold, items and vision are not used.

## Overlay

```
py -m pip install -r requirements.txt
py lol_overlay.py --rank GOLD
```

- A small gem pops up for 45 seconds after the 10:00 and 15:00 reads, then disappears.
- Clicks pass through it to the game.
- League must be in **Borderless** or Windowed mode. Overlays can't draw over exclusive fullscreen.
- `py lol_overlay.py --demo 15` previews the box with fake data, no game needed.
- Hotkeys: **Ctrl+Alt+D** toggles drag mode to move the box, **Ctrl+Alt+Q** quits.

The overlay also saves a snapshot of each game to `lol_snapshots.csv`, so don't run it
alongside `lol_snapshots.py`.

## Terminal version

```
py lol_snapshots.py --rank GOLD
```

Same polling and snapshots, but prints the win chance instead of showing the overlay.

## Files

| File | What it is |
|---|---|
| `lol_overlay.py` | Always-on-top, click-through overlay (Tkinter + Pillow) |
| `lol_snapshots.py` | Polls Riot's Live Client Data API, takes snapshots, runs the model |
| `win_model.json` | Coefficients for the 10 and 15 minute models |
| `lol_eda_10_vs_15.ipynb` | Exploration, cross-validation and model export |
| `assets/` | Gem sprite and font |

## Data

The training CSVs (`lol_10min_all.csv`, `lol_15min_all.csv`, `lol_10min_silver.csv`) are not
in this repo. The notebook needs them next to it to re-run. Live game data comes from Riot's
local Live Client Data API at `https://127.0.0.1:2999`.

## Credits

Font: Romulus by Hewett Tsoi (https://www.dafont.com/romulus.font).

This project isn't endorsed by Riot Games and doesn't reflect the views of Riot Games or
anyone officially involved in producing or managing Riot Games properties.
