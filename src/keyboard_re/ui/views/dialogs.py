"""
Modal Dialogs for IO by Red Square Type 84 Configurator.

Provides:
- ConfirmationDialog: Preview dry-run write plan summary before applying changes.
- ErrorDialog: Structured error alert with audit details.
"""

from __future__ import annotations

from typing import Any, Callable, Optional
import customtkinter as ctk

from keyboard_re.applicator import PlanConfirmationSummary
from keyboard_re.profile_manager import ProfileDiff
from keyboard_re.protocol.rgb import RGB_EFFECT_CATALOG
from keyboard_re.ui.layout_data import KEY_BY_LED_SLOT, KEY_BY_REMAP_SLOT


def _format_param_value(parameter: str, value: Any) -> str:
    """Format parameter value with human-readable descriptions where appropriate."""
    if parameter == "effect":
        try:
            val_int = int(value)
            meta = RGB_EFFECT_CATALOG.get(val_int)
            if meta:
                return f"0x{val_int:02X} ({meta.name_en})"
            return f"0x{val_int:02X} (Unknown)"
        except (ValueError, TypeError):
            return str(value)
    return str(value)


class ConfirmationDialog(ctk.CTkToplevel):
    """
    Modal confirmation dialog displaying dry-run Write Plan details
    and explicit hardware write warning before applying profile.
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        summary: PlanConfirmationSummary,
        diff: Optional[ProfileDiff] = None,
        on_confirm: Optional[Callable[[bool], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self.summary = summary
        self.diff = diff
        self.on_confirm = on_confirm

        self.title("Confirm Changes Application")
        self.geometry("540x480")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self._build_widgets()

    def _build_widgets(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # 1. Header
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")

        title = ctk.CTkLabel(
            header_frame,
            text="Profile Write Plan Summary",
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        title.pack(anchor="w")

        sub = ctk.CTkLabel(
            header_frame,
            text=f"Target: Profile #{self.summary.profile_id} ('{self.summary.profile_name}')",
            font=ctk.CTkFont(size=12),
            text_color="#a1a1aa",
        )
        sub.pack(anchor="w")

        # 2. Key Metrics Bar
        metrics_frame = ctk.CTkFrame(self, fg_color=("#e4e4e7", "#27272a"), corner_radius=6)
        metrics_frame.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="ew")

        subsystems_str = ", ".join(self.summary.changed_subsystems) or "None"
        m1 = ctk.CTkLabel(
            metrics_frame,
            text=f"Subsystems: {subsystems_str}",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        m1.pack(anchor="w", padx=12, pady=(8, 2))

        m2 = ctk.CTkLabel(
            metrics_frame,
            text=f"Total Packets: {self.summary.total_packets} report(s)  |  Total Changes: {self.summary.total_diff_changes}",
            font=ctk.CTkFont(size=12),
            text_color="#93c5fd",
        )
        m2.pack(anchor="w", padx=12, pady=(2, 8))

        # 3. Detailed Changes Scrollable View
        details_box = ctk.CTkTextbox(self, font=ctk.CTkFont(family="Consolas", size=11))
        details_box.grid(row=2, column=0, padx=20, pady=5, sticky="nsew")

        text_lines = ["Planned Hardware Operations:"]
        for idx, step in enumerate(self.summary.steps_summary, 1):
            text_lines.append(
                f"  [{idx}] Opcode 0x{step['opcode']:02X} ({step['subsystem']}): "
                f"{step['packet_count']} packet(s) - {step['description']}"
            )

        if self.diff:
            text_lines.append("\nDetailed Parameter Diffs:")
            for sub_name, sub_diff in self.diff.subsystems.items():
                if sub_diff.has_changes:
                    text_lines.append(f"  • {sub_name.upper()}: {sub_diff.summary}")
                    for d in sub_diff.details[:12]:
                        if hasattr(d, "led_index"):
                            k_def = KEY_BY_LED_SLOT.get(d.led_index)
                            name_str = f"Key '{k_def.label}'" if k_def else f"LED {d.led_index}"
                            old_hex = f"#{d.old_color[0]:02X}{d.old_color[1]:02X}{d.old_color[2]:02X}"
                            new_hex = f"#{d.new_color[0]:02X}{d.new_color[1]:02X}{d.new_color[2]:02X}"
                            text_lines.append(f"      - {name_str} (slot {d.led_index}): {old_hex} -> {new_hex}")
                        elif hasattr(d, "slot_index") and hasattr(d, "new_hid_name"):
                            k_def = KEY_BY_REMAP_SLOT.get(d.slot_index)
                            name_str = f"Key '{k_def.label}'" if k_def else f"Slot {d.slot_index}"
                            old_name = d.old_hid_name if d.old_scancode != 0 else "Default"
                            if d.old_scancode == 0 and d.old_type == 0:
                                old_name = "[Unbound]"
                            new_name = d.new_hid_name if d.new_scancode != 0 else "Default"
                            if d.new_scancode == 0 and d.new_type == 0:
                                new_name = "[Unbound]"
                            text_lines.append(f"      - {name_str} (remap slot {d.slot_index}): {old_name} -> {new_name}")
                        elif hasattr(d, "parameter"):
                            old_str = _format_param_value(d.parameter, d.old_value)
                            new_str = _format_param_value(d.parameter, d.new_value)
                            text_lines.append(f"      - {d.parameter}: {old_str} -> {new_str}")
                        else:
                            text_lines.append(f"      - {d}")
                    if len(sub_diff.details) > 12:
                        text_lines.append(f"      - ... (+{len(sub_diff.details) - 12} more items)")

        details_box.insert("1.0", "\n".join(text_lines))
        details_box.configure(state="disabled")

        # 4. Hardware Warning Alert
        warn_frame = ctk.CTkFrame(self, fg_color=("#fef3c7", "#451a03"), corner_radius=6)
        warn_frame.grid(row=3, column=0, padx=20, pady=10, sticky="ew")

        warn_label = ctk.CTkLabel(
            warn_frame,
            text="⚠️ WARNING: This action will write configuration directly to the physical keyboard.",
            text_color=("#b45309", "#fbbf24"),
            font=ctk.CTkFont(size=11, weight="bold"),
            wraplength=480,
        )
        warn_label.pack(padx=10, pady=8)

        # 5. Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=4, column=0, padx=20, pady=(5, 20), sticky="ew")
        btn_frame.grid_columnconfigure(0, weight=1)

        cancel_btn = ctk.CTkButton(
            btn_frame,
            text="Cancel",
            fg_color="#3f3f46",
            hover_color="#52525b",
            width=110,
            command=self._cancel,
        )
        cancel_btn.grid(row=0, column=1, padx=(0, 10))

        confirm_btn = ctk.CTkButton(
            btn_frame,
            text="Confirm & Apply",
            fg_color="#ef4444",
            hover_color="#dc2626",
            width=140,
            command=self._confirm,
        )
        confirm_btn.grid(row=0, column=2)

    def _confirm(self) -> None:
        self.destroy()
        if self.on_confirm:
            self.on_confirm(True)

    def _cancel(self) -> None:
        self.destroy()
        if self.on_confirm:
            self.on_confirm(False)


class ErrorDialog(ctk.CTkToplevel):
    """Simple error dialog showing operation failure message."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        title_text: str,
        message: str,
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self.title(title_text)
        self.geometry("420x220")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        lbl = ctk.CTkLabel(
            self,
            text=f"❌ {title_text}",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#ef4444",
        )
        lbl.grid(row=0, column=0, padx=20, pady=(20, 5), sticky="w")

        msg = ctk.CTkLabel(
            self,
            text=message,
            font=ctk.CTkFont(size=12),
            wraplength=380,
            justify="left",
        )
        msg.grid(row=1, column=0, padx=20, pady=10, sticky="nw")

        btn = ctk.CTkButton(
            self,
            text="Close",
            width=100,
            command=self.destroy,
        )
        btn.grid(row=2, column=0, padx=20, pady=(0, 20), sticky="e")
