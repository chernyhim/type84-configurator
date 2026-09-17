"""
Main Window View for IO by Red Square Type 84 Configurator.

Coordinates:
- Header (Connection controls: Physical / Mock)
- Interactive Keyboard View (75% Type 84)
- Subsystem Editor Tabs (RGB Global active in vertical slice 1)
- Status Bar & Apply Action
"""

from __future__ import annotations

import customtkinter as ctk

from keyboard_re.ui.controller import AppController
from keyboard_re.ui.views.dialogs import ConfirmationDialog, ErrorDialog
from keyboard_re.ui.views.dks_view import DKSView
from keyboard_re.ui.views.hall_view import HallView
from keyboard_re.ui.views.keyboard_view import KeyboardView
from keyboard_re.ui.views.macros_view import MacrosView
from keyboard_re.ui.views.per_key_rgb_view import PerKeyRGBView
from keyboard_re.ui.views.remap_view import KeyRemapView
from keyboard_re.ui.views.rgb_global_view import RGBGlobalView
from keyboard_re.ui.views.settings_view import SettingsView
from keyboard_re.ui.views.status_bar import StatusBarView


class MainWindow(ctk.CTkFrame):
    """Root application frame hosting all views and workflow dialogs."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: AppController,
        **kwargs,
    ) -> None:
        super().__init__(master, corner_radius=0, fg_color="transparent", **kwargs)
        self.controller = controller

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_keyboard_section()
        self._build_tabs_section()
        self._build_status_bar()

        # Subscribe to controller events
        self.controller.subscribe(self._on_controller_update)

    def _build_header(self) -> None:
        """Build top navigation and connection bar."""
        header = ctk.CTkFrame(self, height=50, corner_radius=0, fg_color=("#e4e4e7", "#18181b"))
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(
            header,
            text="IO Type 84 Configurator",
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        title.grid(row=0, column=0, padx=15, pady=10, sticky="w")

        btn_box = ctk.CTkFrame(header, fg_color="transparent")
        btn_box.grid(row=0, column=1, padx=15, pady=8, sticky="e")

        self.save_profile_btn = ctk.CTkButton(
            btn_box,
            text="Save Profile...",
            width=105,
            fg_color="#334155",
            hover_color="#475569",
            command=self._on_save_profile,
            state="disabled",
        )
        self.save_profile_btn.grid(row=0, column=0, padx=(0, 6))

        self.load_profile_btn = ctk.CTkButton(
            btn_box,
            text="Load Profile...",
            width=105,
            fg_color="#334155",
            hover_color="#475569",
            command=self._on_load_profile,
            state="disabled",
        )
        self.load_profile_btn.grid(row=0, column=1, padx=(0, 14))

        self.connect_btn = ctk.CTkButton(
            btn_box,
            text="Connect USB",
            width=110,
            command=self._on_connect_physical,
        )
        self.connect_btn.grid(row=0, column=2, padx=(0, 6))

        self.mock_btn = ctk.CTkButton(
            btn_box,
            text="Connect Mock",
            width=110,
            fg_color="#0284c7",
            hover_color="#0369a1",
            command=self._on_connect_mock,
        )
        self.mock_btn.grid(row=0, column=3, padx=(0, 6))

        self.disconnect_btn = ctk.CTkButton(
            btn_box,
            text="Disconnect",
            width=90,
            fg_color="#3f3f46",
            hover_color="#52525b",
            command=self._on_disconnect,
            state="disabled",
        )
        self.disconnect_btn.grid(row=0, column=4)

    def _build_keyboard_section(self) -> None:
        """Build visual keyboard view."""
        self.keyboard_view = KeyboardView(self, controller=self.controller)
        self.keyboard_view.grid(row=1, column=0, padx=15, pady=(10, 5), sticky="ew")

    def _build_tabs_section(self) -> None:
        """Build subsystem editor tabview."""
        self.tabview = ctk.CTkTabview(self, corner_radius=8, command=self._on_tab_changed)
        self.tabview.grid(row=2, column=0, padx=15, pady=5, sticky="nsew")

        # 1. RGB Global (Active in slice 1)
        self.tabview.add("RGB Global")
        self.rgb_view = RGBGlobalView(
            self.tabview.tab("RGB Global"),
            controller=self.controller,
        )
        self.rgb_view.pack(fill="both", expand=True, padx=5, pady=5)

        # 2. Per-Key RGB (Active in slice 2)
        self.tabview.add("Per-Key RGB")
        self.per_key_view = PerKeyRGBView(
            self.tabview.tab("Per-Key RGB"),
            controller=self.controller,
        )
        self.per_key_view.pack(fill="both", expand=True, padx=5, pady=5)

        # 3. Key Remap (L1/L2) (Active in slice 3)
        self.tabview.add("Key Remap (L1/L2)")
        self.remap_view = KeyRemapView(
            self.tabview.tab("Key Remap (L1/L2)"),
            controller=self.controller,
        )
        self.remap_view.pack(fill="both", expand=True, padx=5, pady=5)

        # 4. Hall Effect / Rapid Trigger (Active in slice 4)
        self.tabview.add("Hall / Rapid Trigger")
        self.hall_view = HallView(
            self.tabview.tab("Hall / Rapid Trigger"),
            controller=self.controller,
        )
        self.hall_view.pack(fill="both", expand=True, padx=5, pady=5)

        # 5. DKS (Dynamic Keystrokes)
        self.tabview.add("DKS")
        self.dks_view = DKSView(
            self.tabview.tab("DKS"),
            controller=self.controller,
        )
        self.dks_view.pack(fill="both", expand=True, padx=5, pady=5)

        # 6. Macros (AA 15 / AA 25)
        self.tabview.add("Macros")
        self.macros_view = MacrosView(
            self.tabview.tab("Macros"),
            controller=self.controller,
        )
        self.macros_view.pack(fill="both", expand=True, padx=5, pady=5)

        # 7. Settings / Game Mode (AA 11 / AA 21)
        self.tabview.add("Settings")
        self.settings_view = SettingsView(
            self.tabview.tab("Settings"),
            controller=self.controller,
        )
        self.settings_view.pack(fill="both", expand=True, padx=5, pady=5)

        self.tabview.set("RGB Global")

    def _build_status_bar(self) -> None:
        """Build status bar footer."""
        self.status_bar = StatusBarView(
            self,
            controller=self.controller,
            on_apply_clicked=self._on_apply_clicked,
            on_discard_clicked=self._on_discard_clicked,
        )
        self.status_bar.grid(row=3, column=0, sticky="ew")

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------

    def _on_connect_physical(self) -> None:
        self.connect_btn.configure(text="Connecting...", state="disabled")
        self.mock_btn.configure(state="disabled")
        self.update_idletasks()
        try:
            success = self.controller.connect(use_mock=False)
            if not success:
                self.connect_btn.configure(text="Connect USB", state="normal")
                self.mock_btn.configure(state="normal")
                ErrorDialog(
                    self,
                    title_text="Physical Connection Failed",
                    message="Could not connect to IO Type 84 keyboard. Check USB connection or try 'Connect Mock'.",
                )
            else:
                self.connect_btn.configure(text="Connect USB", state="disabled")
                self.mock_btn.configure(state="disabled")
                self.disconnect_btn.configure(state="normal")
        except Exception as ex:
            self.connect_btn.configure(text="Connect USB", state="normal")
            self.mock_btn.configure(state="normal")
            ErrorDialog(
                self,
                title_text="Physical Connection Failed",
                message=f"Connection error: {ex}",
            )

    def _on_connect_mock(self) -> None:
        self.controller.connect(use_mock=True)

    def _on_disconnect(self) -> None:
        self.controller.disconnect()

    def _on_apply_clicked(self) -> None:
        """Open ConfirmationDialog with dry-run write plan."""
        summary = self.controller.get_confirmation_summary()
        if not summary:
            return

        diff = self.controller.last_diff
        ConfirmationDialog(
            master=self,
            summary=summary,
            diff=diff,
            on_confirm=self._execute_apply,
        )

    def _execute_apply(self, confirmed: bool) -> None:
        if not confirmed:
            return

        result = self.controller.apply_changes(confirmed=True)
        if not result.is_success:
            ErrorDialog(
                self,
                title_text="Apply Failed",
                message=result.error or "Unknown error while applying profile to device.",
            )

    def _on_save_profile(self) -> None:
        """Export current working profile to a JSON file."""
        if not self.controller.working_profile:
            return
        from tkinter import filedialog
        default_name = f"profile_{self.controller.working_profile.profile_id}.json"
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("Profile JSON", "*.json"), ("All Files", "*.*")],
            title="Save Profile to File",
        )
        if path:
            try:
                self.controller.save_profile_to_disk(path)
            except Exception as ex:
                ErrorDialog(self, title_text="Save Profile Failed", message=str(ex))

    def _on_load_profile(self) -> None:
        """Load a profile from a JSON file into working profile."""
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            filetypes=[("Profile JSON", "*.json"), ("All Files", "*.*")],
            title="Load Profile from File",
        )
        if path:
            try:
                self.controller.load_profile_from_disk(path)
            except Exception as ex:
                ErrorDialog(self, title_text="Load Profile Failed", message=str(ex))

    def _on_discard_clicked(self) -> None:
        """Discard all unsaved working profile changes."""
        self.controller.discard_all_changes()

    def _on_controller_update(self) -> None:
        """Update view widgets in response to controller event."""
        # Update connection and profile buttons
        if self.controller.is_connected:
            self.connect_btn.configure(state="disabled")
            self.mock_btn.configure(state="disabled")
            self.disconnect_btn.configure(state="normal")
            self.save_profile_btn.configure(state="normal")
            self.load_profile_btn.configure(state="normal")
        else:
            self.connect_btn.configure(state="normal")
            self.mock_btn.configure(state="normal")
            self.disconnect_btn.configure(state="disabled")
            self.save_profile_btn.configure(state="disabled")
            self.load_profile_btn.configure(state="disabled")

        # Refresh subviews
        self.keyboard_view.update_display()
        self.rgb_view.update_display()
        self.per_key_view.update_display()
        if hasattr(self, "remap_view"):
            self.remap_view.update_display()
        if hasattr(self, "hall_view"):
            self.hall_view.update_display()
        if hasattr(self, "dks_view"):
            self.dks_view.update_display()
        if hasattr(self, "macros_view"):
            self.macros_view.update_display()
        if hasattr(self, "settings_view"):
            self.settings_view.update_display()
        self.status_bar.update_display()

    def _on_tab_changed(self) -> None:
        """Handle switching tabs in TabView."""
        selected_tab = self.tabview.get()
        tab_map = {
            "RGB Global": "rgb_global",
            "Per-Key RGB": "per_key_rgb",
            "Key Remap (L1/L2)": "remap",
            "Hall / Rapid Trigger": "hall",
            "DKS": "dks",
            "Macros": "macros",
            "Settings": "settings",
        }
        active_id = tab_map.get(selected_tab, "rgb_global")
        self.controller.set_active_tab(active_id)
        self.keyboard_view.update_display()
        if hasattr(self, "per_key_view"):
            self.per_key_view.update_display()
        if hasattr(self, "remap_view"):
            self.remap_view.update_display()
        if hasattr(self, "hall_view"):
            self.hall_view.update_display()
        if hasattr(self, "dks_view"):
            self.dks_view.update_display()
        if hasattr(self, "macros_view"):
            self.macros_view.update_display()
        if hasattr(self, "settings_view"):
            self.settings_view.update_display()
