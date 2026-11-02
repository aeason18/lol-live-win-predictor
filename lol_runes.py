"""Rune icons from Riot's Data Dragon, cached like the item data."""

import json

import requests

from lol_items import CACHE, DDRAGON, _fetch_json


def _trees(version):
    """runesReforged.json: from the cache, the network, or [] if neither works."""
    path = CACHE / version / "runesReforged.json"
    try:
        if not path.exists():
            data = _fetch_json(f"{DDRAGON}/cdn/{version}/data/en_US/runesReforged.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), encoding="utf-8")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, requests.RequestException):
        return []


def load_icon_paths(version):
    """{rune or tree id: icon path under Data Dragon's /cdn/img/}."""
    paths = {}
    for tree in _trees(version):
        paths[tree["id"]] = tree["icon"]
        for slot in tree["slots"]:
            for rune in slot["runes"]:
                paths[rune["id"]] = rune["icon"]
    return paths


def load_tree_names(version):
    """{rune tree id: name} (Precision, Domination, ...)."""
    return {tree["id"]: tree["name"] for tree in _trees(version)}
