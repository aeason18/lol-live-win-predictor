"""Open the champion lookup window with real data, no League client needed.

    py lookup_demo.py            # opens on Jhin
    py lookup_demo.py "Lee Sin"  # opens on that champion

It aggregates the games collected so far by build_data.py (data/raw/) in memory, so it doesn't
touch data/builds.json. Close the window to exit.
"""

import sys
import tkinter as tk

import build_data
from lol_items import Catalog
from lol_lookup import Lookup


def main():
    records = build_data.load_records("na1")
    if not records:
        sys.exit("No collected games in data/raw/ yet; run build_data.py first.")
    builds = {"champions": build_data.aggregate(records)}
    catalog = Catalog.load()
    root = tk.Tk()
    root.withdraw()
    lookup = Lookup(root, builds, catalog.champ_names, catalog.version, lambda x, y: root.quit())
    wanted = " ".join(sys.argv[1:]) or "Jhin"
    found = lookup._matching(wanted)
    lookup.choose(found[0][1] if found else "Jhin")
    root.mainloop()


if __name__ == "__main__":
    main()
