"""Talks to the League client's local API (the LCU) to learn which phase you're in.

Not the in-game Live Client API: this one belongs to the client window, and it's how the
overlay knows champion select has started. It only listens on this PC, with a port and a
password that the client picks each time it starts. Read-only: this module never changes
anything in the client.
"""

import base64
import re
import subprocess
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)  # the client's cert is self-signed

LOCKFILES = [Path(f"{drive}:/Riot Games/League of Legends/lockfile") for drive in "CDEFG"]
PS_QUERY = ("Get-CimInstance Win32_Process -Filter \"Name='LeagueClientUx.exe'\" "
            "| Select-Object -First 1 -ExpandProperty CommandLine")
RECHECK = 15  # seconds between hunts for the client when it isn't running


def parse_lockfile(text):
    """'LeagueClient:pid:port:password:https' -> (port, password)."""
    parts = text.strip().split(":")
    return int(parts[2]), parts[3]


def parse_command_line(cmd):
    """The client's own command line carries the port and password as flags."""
    port = re.search(r"--app-port=(\d+)", cmd or "")
    token = re.search(r"--remoting-auth-token=([\w-]+)", cmd or "")
    return (int(port.group(1)), token.group(1)) if port and token else None


def find_credentials():
    for path in LOCKFILES:
        try:
            return parse_lockfile(path.read_text(encoding="utf-8"))
        except (OSError, IndexError, ValueError):
            continue
    try:  # installed somewhere unusual: read the running client's command line instead
        out = subprocess.run(["powershell", "-NoProfile", "-Command", PS_QUERY], capture_output=True,
                             text=True, timeout=15, creationflags=0x08000000).stdout  # no console window
        return parse_command_line(out)
    except (OSError, subprocess.SubprocessError):
        return None


class Client:
    def __init__(self):
        self.creds = None
        self._next_search = 0.0
        self.session = requests.Session()

    def get(self, path):
        """Parsed JSON for a client endpoint, or None if the client isn't reachable."""
        if not self.creds:
            if time.monotonic() < self._next_search:
                return None
            self.creds = find_credentials()
            self._next_search = time.monotonic() + RECHECK
            if not self.creds:
                return None
        port, password = self.creds
        auth = base64.b64encode(f"riot:{password}".encode()).decode()
        try:
            resp = self.session.get(f"https://127.0.0.1:{port}{path}", verify=False, timeout=3,
                                    headers={"Authorization": f"Basic {auth}"})
            if resp.status_code == 200:
                return resp.json()
        except (requests.RequestException, ValueError):
            pass
        self.creds = None  # the client restarted (new port and password) or closed
        return None

    def phase(self):
        """'ChampSelect', 'Lobby', 'InProgress', ... or None when the client isn't running."""
        return self.get("/lol-gameflow/v1/gameflow-phase")
