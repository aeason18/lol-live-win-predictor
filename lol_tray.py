"""System-tray icon: the overlay's only UI once it runs without a terminal."""

import pystray
from PIL import Image
from pystray import Menu, MenuItem

import lol_startup


def _icon_image(sprite):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    gem = sprite.resize((64, 32), Image.NEAREST)
    img.paste(gem, (0, 16), gem)
    return img


def start_tray(sprite, send):
    """Show the tray icon on its own thread. `send(cmd)` hands "drag" or "quit" to the overlay."""
    def toggle_startup(icon, item):
        lol_startup.set_enabled(not lol_startup.enabled())

    menu = Menu(
        MenuItem("Move the box (Ctrl+Alt+D)", lambda icon, item: send("drag")),
        MenuItem("Start with Windows", toggle_startup, checked=lambda item: lol_startup.enabled()),
        MenuItem("Quit (Ctrl+Alt+Q)", lambda icon, item: send("quit")),
    )
    icon = pystray.Icon("LoLWinOverlay", _icon_image(sprite), "LoL win overlay", menu)
    icon.run_detached()
    return icon
