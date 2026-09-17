"""
Status Bar View for IO by Red Square Type 84 Configurator.

Displays:
- Connection Status (Connected / Disconnected / Mock)
- Device VID:PID (0x0C45:0x80D6)
- Synchronization / Dirty State (Synchronized vs Unsaved changes count)
- Apply Button
"""

from __future__ import annotations

from typing import Callable, Optional
import customtkinter as ctk

from keyboard_re.ui.controller import AppController


class StatusBarView(ctk.CTkFrame):
    """Bottom status bar displaying connection, sync state, and apply action."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: AppController,
        on_apply_clicked: Optional[Callable[[], None]] = None,
        on_discard_clicked: Optional[Callable[[], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, height=45, corner_radius=0, fg_color=("#e4e4e7", "#18181b"), **kwargs)
        self.controller = controller
        self.on_apply_clicked = on_apply_clicked
        self.on_discard_clicked = on_discard_clicked

        self.grid_columnconfigure(1, weight=1)

        # Connection status badge
        self.status_badge = ctk.CTkLabel(
            self,
            text="● Disconnected",
            text_color="#ef4444",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.status_badge.grid(row=0, column=0, padx=(15, 10), pady=8, sticky="w")

        # Sync / Dirty state indicator
        self.sync_label = ctk.CTkLabel(
            self,
            text="⚪ Synchronized",
            text_color="#71717a",
            font=ctk.CTkFont(size=12),
        )
        self.sync_label.grid(row=0, column=1, padx=10, pady=8, sticky="w")

        # Actions box
        actions_box = ctk.CTkFrame(self, fg_color="transparent")
        actions_box.grid(row=0, column=2, padx=(10, 15), pady=8, sticky="e")

        # Discard button
        self.discard_btn = ctk.CTkButton(
            actions_box,
            text="Discard Changes",
            width=130,
            height=30,
            fg_color="#3f3f46",
            hover_color="#52525b",
            font=ctk.CTkFont(size=12),
            command=self._handle_discard,
            state="disabled",
        )
        self.discard_btn.pack(side="left", padx=(0, 8))

        # Apply button
        self.apply_btn = ctk.CTkButton(
            actions_box,
            text="Apply Changes",
            width=140,
            height=30,
            fg_color="#3b82f6",
            hover_color="#2563eb",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._handle_apply,
            state="disabled",
        )
        self.apply_btn.pack(side="left")

        self.update_display()

    def update_display(self) -> None:
        """Update status bar widgets based on current controller state."""
        # 1. Connection
        if self.controller.is_connected:
            color = "#22c55e" if not self.controller.is_mock else "#06b6d4"
            self.status_badge.configure(
                text=f"● {self.controller.connection_status_text}",
                text_color=color,
            )
        else:
            self.status_badge.configure(
                text="● Disconnected",
                text_color="#ef4444",
            )

        # 2. Dirty / Sync state
        if not self.controller.is_connected:
            self.sync_label.configure(text="", text_color="#71717a")
            self.discard_btn.configure(state="disabled")
            self.apply_btn.configure(state="disabled")
        elif self.controller.is_dirty:
            self.sync_label.configure(
                text=f"🟠 Unsaved changes ({self.controller.diff_count} modified)",
                text_color="#f59e0b",
            )
            self.discard_btn.configure(state="normal")
            self.apply_btn.configure(state="normal")
        else:
            self.sync_label.configure(
                text="🟢 Synchronized with keyboard",
                text_color="#10b981",
            )
            self.discard_btn.configure(state="disabled")
            self.apply_btn.configure(state="disabled")

    def _handle_apply(self) -> None:
        if self.on_apply_clicked:
            self.on_apply_clicked()

    def _handle_discard(self) -> None:
        if self.on_discard_clicked:
            self.on_discard_clicked()
        else:
            self.controller.discard_all_changes()
