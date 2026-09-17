"""
UI Views package for IO by Red Square Type 84 Configurator.
"""

from keyboard_re.ui.views.dialogs import ConfirmationDialog, ErrorDialog
from keyboard_re.ui.views.keyboard_view import KeyboardView
from keyboard_re.ui.views.main_window import MainWindow
from keyboard_re.ui.views.macros_view import MacrosView
from keyboard_re.ui.views.per_key_rgb_view import PerKeyRGBView
from keyboard_re.ui.views.remap_view import KeyRemapView
from keyboard_re.ui.views.rgb_global_view import RGBGlobalView
from keyboard_re.ui.views.settings_view import SettingsView
from keyboard_re.ui.views.status_bar import StatusBarView
from keyboard_re.ui.views.visualizer_view import VisualizerView

__all__ = [
    "MainWindow",
    "KeyboardView",
    "RGBGlobalView",
    "PerKeyRGBView",
    "KeyRemapView",
    "MacrosView",
    "SettingsView",
    "StatusBarView",
    "VisualizerView",
    "ConfirmationDialog",
    "ErrorDialog",
]
