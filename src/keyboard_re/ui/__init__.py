"""
User Interface package for IO by Red Square Type 84 Magnetic Black.

Strict Architecture Boundary:
- UI components communicate exclusively with AppController and high-level domain models.
- NO direct imports of protocol report IDs (AAxx), packet builders, chunkers, or raw HID writes.
"""

from keyboard_re.ui.controller import AppController

__all__ = ["KeyboardApp", "AppController"]


def __getattr__(name: str):
    if name == "KeyboardApp":
        from keyboard_re.ui.app import KeyboardApp
        return KeyboardApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
