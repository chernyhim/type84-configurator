"""
Top-level Application Window for IO by Red Square Type 84 Configurator.
"""

from __future__ import annotations

import sys
from typing import Optional
import customtkinter as ctk

from keyboard_re.ui.controller import AppController
from keyboard_re.ui.views.main_window import MainWindow


class KeyboardApp(ctk.CTk):
    """Root CustomTkinter application window."""

    def __init__(
        self,
        controller: Optional[AppController] = None,
        auto_connect_mock: bool = False,
    ) -> None:
        super().__init__()

        # Appearance setup
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("IO Type 84 Configurator (Reverse-Engineered Safe GUI)")
        self.geometry("980x760")
        self.minsize(920, 720)

        # Controller & View
        self.controller = controller or AppController()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.main_window = MainWindow(self, controller=self.controller)
        self.main_window.grid(row=0, column=0, sticky="nsew")

        if auto_connect_mock:
            self.controller.connect(use_mock=True)

    def run(self) -> None:
        """Start GUI main event loop."""
        self.mainloop()


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint for GUI launch."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="type84-gui",
        description="Safe GUI configurator for IO by Red Square Type 84 Magnetic Black keyboard.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="type84-gui 1.0.0-rc1 (CB-1/CB-3/RGB-Global verified)",
    )
    parser.add_argument("--mock", action="store_true", help="Launch GUI with mock device pre-connected")
    args = parser.parse_args(argv)

    app = KeyboardApp(auto_connect_mock=args.mock)
    app.run()


if __name__ == "__main__":
    main()
