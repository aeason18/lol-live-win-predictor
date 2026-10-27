"""Where files live: bundled read-only resources vs. the user's writable data.

Run from source, both are the project folder. Run as the packaged .exe, resources are inside
the bundle and everything the app writes goes to %LOCALAPPDATA%/LoLWinOverlay.
"""

import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
USER_DIR = (Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "LoLWinOverlay"
            if FROZEN else Path(__file__).parent)
