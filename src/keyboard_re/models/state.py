"""
Unified DeviceState & Profile Architecture for IO by Red Square Type 84 Magnetic Black.

Minimal, rigorous state model integrating all 10 confirmed & observed subsystems:
1. Device Info (AA 10, 64B) -> DeviceInfoResponse
2. GameMode / Performance Settings (AA 11, 64B) -> GameModeResponse
3. Hall Profile 1 Switch Thresholds (AA 17, 1008B) -> HallProfileState
4. DKS Table (AA 18, 1024B) -> raw bytes (64 records * 16B)
5. Key Remap Layer 1 Base (AA 12, 512B) -> KeymapTable
6. Key Remap Layer 2 Fn (AA 16, 512B) -> KeymapTable
7. Layer 3 raw table (AA 1C, 512B) -> raw bytes (UNKNOWN firmware semantics)
8. Global RGB config (AA 13, 64B) -> RGBGlobalConfig
9. RGB Matrix / Per-Key buffer (AA 14, 512B) -> raw bytes
10. Macro raw table (AA 15, 400B) -> raw bytes

Design principles:
- Confirmed semantics have structured properties (actuation_mm, scancodes, effect, colors).
- Unknown semantics (flags, macro format, AA 1C layer details, DKS internal record structure) are stored as raw/UNKNOWN.
- Byte-exact preservation: dump_raw_buffers() guarantees 100% round-trip binary equality.
- Profile abstraction: software-defined host-side profiles (re-flashed sequentially).
- Zero physical writes: collect_device_state() is strictly read-only.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
from pathlib import Path
import struct
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Union

from keyboard_re.models.base import (
    CONFIG_IMAGE_SIZE,
    KEY_MAP,
    KEY_RECORD_SIZE,
    KeyConfig,
    key_address,
    resolve_key_name,
)
from keyboard_re.protocol.keymap import (
    KEYMAP_BUFFER_SIZE,
    KEYMAP_RECORD_SIZE,
    KEYMAP_SLOT_COUNT,
    KeyRemapRecord,
    KeymapTable,
    parse_keymap_chunks,
)
from keyboard_re.protocol.macro import MacroTable, parse_macro_chunks
from keyboard_re.protocol.packets import REPORT_SIZE, pad_report, validate_report_size
from keyboard_re.protocol.read import (
    DKS_BUFFER_SIZE,
    DKS_RECORD_COUNT,
    DKS_RECORD_SIZE,
    DeviceInfoResponse,
    GameModeResponse,
    KeyboardStatusResponse,
    parse_calibration_response,
    parse_dks_read_chunks,
    parse_game_mode_response,
    parse_hall_read_chunks,
    parse_handshake_response,
    parse_rgb_global_read_response,
    parse_rgb_per_key_read_chunks,
    parse_status_response,
    read_device_info,
    read_dks_table,
    read_game_mode,
    read_hall_profile,
    read_keyboard_status,
    read_keymap_table,
    read_macro_table,
    read_rgb_global,
    read_rgb_per_key,
)
from keyboard_re.protocol.rgb import RGBGlobalConfig
from keyboard_re.protocol.transport import HidTransport

if TYPE_CHECKING:
    from keyboard_re.protocol.diff import StateDiff


@dataclass
class HallProfileState:
    """
    Configuration of Hall Effect switch analog thresholds for a single profile (1008 bytes).
    
    Fields:
    - Actuation Point (Word 0): CONFIRMED (0.01 mm scale)
    - RT Press Sensitivity (Word 1): CONFIRMED (0.01 mm scale, 0 = OFF)
    - RT Release Sensitivity (Word 2): CONFIRMED (0.01 mm scale, 0 = OFF)
    - Flags / Mode (Word 3): UNKNOWN (retained as raw uint16 without interpretation)
    """
    profile_id: int
    raw_image: bytearray
    keys: Dict[str, KeyConfig] = field(default_factory=dict)

    def get_key(self, name: str) -> KeyConfig:
        canon = resolve_key_name(name)
        if not canon or canon not in self.keys:
            raise KeyError(f"Key '{name}' not found in Hall matrix")
        return self.keys[canon]

    def set_key(self, name: str, config: KeyConfig) -> None:
        canon = resolve_key_name(name)
        if not canon or canon not in KEY_MAP:
            raise KeyError(f"Key '{name}' not found in KEY_MAP")
        self.keys[canon] = config
        bank, col = KEY_MAP[canon]
        addr = key_address(bank, col)
        self.raw_image[addr : addr + KEY_RECORD_SIZE] = config.to_bytes()

    def set_actuation(self, name: str, mm: float) -> None:
        cfg = self.get_key(name)
        cfg.actuation_mm = mm
        self.set_key(name, cfg)

    def set_rt(self, name: str, press_mm: float, release_mm: float) -> None:
        cfg = self.get_key(name)
        cfg.enable_rt(press_mm, release_mm)
        self.set_key(name, cfg)

    def disable_rt(self, name: str) -> None:
        cfg = self.get_key(name)
        cfg.disable_rt()
        self.set_key(name, cfg)

    def to_bytes(self) -> bytes:
        return bytes(self.raw_image)

    def clone(self) -> HallProfileState:
        return HallProfileState(
            profile_id=self.profile_id,
            raw_image=bytearray(self.raw_image),
            keys={k: KeyConfig(v.actuation, v.rt_press, v.rt_release, v.flags) for k, v in self.keys.items()},
        )

    @classmethod
    def from_bytes(
        cls,
        image_bytes: bytes | bytearray | int | None = None,
        profile_id: int = 1,
        *args,
        **kwargs,
    ) -> HallProfileState:
        if isinstance(image_bytes, int):
            pid = image_bytes
            raw_data = bytes(args[0]) if args else kwargs.get("image_bytes")
        else:
            pid = kwargs.get("profile_id", profile_id)
            raw_data = image_bytes if image_bytes is not None else kwargs.get("image_bytes")

        if raw_data is None:
            raise ValueError("image_bytes must be provided")

        raw_bytes = bytes(raw_data)
        if len(raw_bytes) < CONFIG_IMAGE_SIZE:
            raise ValueError(
                f"Hall image requires at least {CONFIG_IMAGE_SIZE} bytes, got {len(raw_bytes)}"
            )
        raw = bytearray(raw_bytes[:CONFIG_IMAGE_SIZE])
        keys: Dict[str, KeyConfig] = {}
        for key_name, (bank, col) in KEY_MAP.items():
            addr = key_address(bank, col)
            slot_bytes = raw[addr : addr + KEY_RECORD_SIZE]
            keys[key_name] = KeyConfig.from_bytes(slot_bytes)
        return cls(profile_id=pid, raw_image=raw, keys=keys)


@dataclass
class Profile:
    """
    Profile abstraction bundling per-profile settings:
    - Hall Effect switch thresholds (HallProfileState, 1008 bytes)
    - Key Remap Layer 1 (KeymapTable, 512 bytes)
    - Key Remap Layer 2 / Fn (KeymapTable, 512 bytes)
    - Optional RGB Global / RGB Matrix / Macros / DKS
    """
    profile_id: int
    hall: HallProfileState
    remap: KeymapTable
    rgb_global: Optional[RGBGlobalConfig] = None
    rgb_matrix: Optional[bytes] = None  # 512-byte per-key LED buffer
    macros_raw: Optional[bytes] = None  # 400-byte macro buffer
    dks_raw: Optional[bytes] = None     # 1024-byte DKS buffer
    remap_l2: Optional[KeymapTable] = None  # 512-byte Fn layer
    game_mode: Optional[GameModeResponse] = None  # Device settings / GameMode (AA 11 / AA 21)
    name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def keymap(self) -> KeymapTable:
        """Alias for remap (backwards compatibility with KeyboardProfile)."""
        return self.remap

    @property
    def remap_l1(self) -> KeymapTable:
        """Alias for remap (Layer 1 Base)."""
        return self.remap

    @property
    def fn_layer(self) -> Optional[KeymapTable]:
        """Alias for remap_l2 (Layer 2 Fn)."""
        return self.remap_l2

    @property
    def rgb_per_key(self) -> Optional[bytes]:
        """Alias for rgb_matrix (backwards compatibility)."""
        return self.rgb_matrix

    @property
    def macros(self) -> Optional[MacroTable]:
        """Parsed MacroTable view of macros_raw if at least 400 bytes."""
        if self.macros_raw and len(self.macros_raw) >= 400:
            catalog = self.macros_raw[:400]
            user_payload = catalog[:392]
            has_user_macros = any(b != 0 for b in user_payload)
            version_flag = catalog[393] if len(catalog) > 393 else 0
            return MacroTable(
                raw_bytes=catalog,
                has_user_macros=has_user_macros,
                version_flag=version_flag,
            )
        return None

    @property
    def macro_catalog(self) -> Any:
        """Parsed MacroCatalog from macros_raw."""
        from keyboard_re.protocol.macro import MacroCatalog
        if self.macros_raw:
            names = self.metadata.get("macro_names", {})
            names = {int(k): str(v) for k, v in names.items()}
            return MacroCatalog.from_full_image(self.macros_raw, names=names)
        return MacroCatalog()

    def set_macro_catalog(self, catalog: Any) -> None:
        """Serialize MacroCatalog into macros_raw and store names in metadata."""
        self.macros_raw = catalog.to_full_image()
        names = {str(mid): m.name for mid, m in catalog.macros.items() if m.name}
        if names:
            self.metadata["macro_names"] = names
        elif "macro_names" in self.metadata:
            del self.metadata["macro_names"]

    @property
    def dks(self) -> Optional[Any]:
        """Parsed DKSTable view of dks_raw if 1024 bytes."""
        if self.dks_raw and len(self.dks_raw) == 1024:
            from keyboard_re.models.dks import DKSTable
            return DKSTable.from_bytes(self.dks_raw)
        return None

    def set_dks_table(self, table: Any) -> None:
        """Serialize DKSTable into dks_raw."""
        self.dks_raw = table.to_bytes()

    def get_key(self, name: str) -> KeyConfig:
        """Helper to access Hall key configuration."""
        return self.hall.get_key(name)

    def clone(self) -> Profile:
        return Profile(
            profile_id=self.profile_id,
            hall=self.hall.clone() if self.hall else None,
            remap=KeymapTable(
                layer=self.remap.layer,
                slots=dict(self.remap.slots),
                raw_bytes=self.remap.raw_bytes,
            ) if self.remap else None,
            rgb_global=copy.deepcopy(self.rgb_global) if self.rgb_global else None,
            rgb_matrix=bytes(self.rgb_matrix) if self.rgb_matrix else None,
            macros_raw=bytes(self.macros_raw) if self.macros_raw else None,
            dks_raw=bytes(self.dks_raw) if self.dks_raw else None,
            remap_l2=KeymapTable(
                layer=self.remap_l2.layer,
                slots=dict(self.remap_l2.slots),
                raw_bytes=self.remap_l2.raw_bytes,
            ) if self.remap_l2 else None,
            game_mode=self.game_mode.clone() if self.game_mode else None,
            name=self.name,
            metadata=dict(self.metadata),
        )

    def get_rgb_editor(self) -> Any:
        """Create an RGBGlobalEditor loaded with this profile's current global lighting."""
        from keyboard_re.models.rgb_editor import RGBGlobalEditor
        if self.rgb_global is not None:
            return RGBGlobalEditor.from_config(self.rgb_global)
        return RGBGlobalEditor()

    def set_rgb_global(self, config_or_editor: Any) -> None:
        """Assign or build RGB global lighting configuration."""
        if hasattr(config_or_editor, "build"):
            self.rgb_global = config_or_editor.build()
        elif isinstance(config_or_editor, RGBGlobalConfig):
            self.rgb_global = config_or_editor
        else:
            raise TypeError(f"Expected RGBGlobalConfig or RGBGlobalEditor, got {type(config_or_editor)}")

    def set_single_color_ripple(
        self,
        color: Union[Tuple[int, int, int], Sequence[int], str] = (255, 128, 0),
        brightness: int = 5,
        speed: int = 5,
    ) -> None:
        """
        Configure deterministic Single-Color Reactive Ripple (0x0F) in persistent state.
        
        Explicitly sets color_mode = 0, primary = color, brightness = brightness, speed = speed.
        """
        from keyboard_re.models.rgb_editor import RGBGlobalEditor
        editor = RGBGlobalEditor().set_single_color_ripple(color, brightness=brightness, speed=speed)
        self.rgb_global = editor.build()

    def set_rainbow_ripple(
        self,
        brightness: int = 5,
        speed: int = 5,
    ) -> None:
        """
        Configure deterministic Rainbow Reactive Ripple (0x0F) in persistent state.
        
        Explicitly sets color_mode = 1, primary = (255, 255, 255), brightness = brightness, speed = speed.
        """
        from keyboard_re.models.rgb_editor import RGBGlobalEditor
        from keyboard_re.protocol.rgb import EFFECT_RIPPLE_SPREAD
        editor = (
            RGBGlobalEditor()
            .set_effect(EFFECT_RIPPLE_SPREAD)
            .set_rainbow()
            .set_brightness(brightness)
            .set_speed(speed)
        )
        self.rgb_global = editor.build()

    def get_rgb_matrix(self) -> Any:
        """Get the RGBMatrix representation of this profile's per-key LED buffer."""
        from keyboard_re.models.rgb_matrix import RGBMatrix
        if self.rgb_matrix:
            return RGBMatrix.from_raw(self.rgb_matrix)
        return RGBMatrix()

    def set_rgb_matrix(self, matrix: Any) -> None:
        """Assign or build per-key RGB matrix buffer (512 bytes)."""
        if hasattr(matrix, "to_raw"):
            self.rgb_matrix = matrix.to_raw()
        elif isinstance(matrix, (bytes, bytearray)):
            if len(matrix) != 512:
                raise ValueError(f"RGB matrix buffer must be exactly 512 bytes, got {len(matrix)}")
            self.rgb_matrix = bytes(matrix)
        else:
            raise TypeError(f"Expected RGBMatrix or bytes, got {type(matrix)}")

    def set_custom_per_key_profile(
        self,
        matrix: Optional[Any] = None,
        brightness: int = 5,
    ) -> None:
        """
        Configure this profile for Custom Per-Key lighting (Effect 0x80).
        
        Coordinates setting effect = 0x80 in rgb_global and assigning the 512-byte
        per-key LED matrix, which ProfileWritePlan requires for custom mode.
        """
        from keyboard_re.models.rgb_editor import RGBGlobalEditor
        from keyboard_re.protocol.rgb import EFFECT_CUSTOM

        editor = (
            RGBGlobalEditor()
            .set_effect(EFFECT_CUSTOM)
            .set_single_color((0, 0, 0))
            .set_brightness(brightness)
        )
        self.rgb_global = editor.build()

        if matrix is not None:
            self.set_rgb_matrix(matrix)
        elif self.rgb_matrix is None:
            from keyboard_re.models.rgb_matrix import RGBMatrix
            self.rgb_matrix = RGBMatrix().to_raw()

    def dump_raw_buffers(self) -> Dict[str, bytes]:
        """
        Export all available raw subsystem buffers for this profile.
        Guarantees 100% byte-exact binary preservation across round-trips.
        """
        bufs: Dict[str, bytes] = {}
        if self.hall:
            bufs["hall_p1"] = self.hall.to_bytes()
        if self.remap:
            bufs["remap_l1"] = self.remap.raw_bytes
        if self.remap_l2:
            bufs["remap_l2"] = self.remap_l2.raw_bytes
        if self.rgb_global:
            if self.rgb_global.raw_payload and len(self.rgb_global.raw_payload) == REPORT_SIZE:
                bufs["rgb_global"] = bytes(self.rgb_global.raw_payload)
            else:
                raw_rgb = bytearray(REPORT_SIZE)
                raw_rgb[0] = 0x55
                raw_rgb[1] = 0x13
                raw_rgb[2] = 0x10
                raw_rgb[3:8] = self.rgb_global.reserved_header
                raw_rgb[8] = self.rgb_global.effect & 0xFF
                raw_rgb[9:12] = bytes(self.rgb_global.primary)
                raw_rgb[12] = self.rgb_global.driver_setting & 0xFF
                raw_rgb[13:16] = bytes(self.rgb_global.secondary)
                raw_rgb[16] = self.rgb_global.color_mode & 0xFF
                raw_rgb[17] = self.rgb_global.brightness & 0xFF
                raw_rgb[18] = self.rgb_global.speed & 0xFF
                raw_rgb[19] = self.rgb_global.direction & 0xFF
                raw_rgb[20] = self.rgb_global.effect_mode_type & 0xFF
                raw_rgb[21] = self.rgb_global.reserved_byte21 & 0xFF
                raw_rgb[22:24] = self.rgb_global.magic
                bufs["rgb_global"] = bytes(raw_rgb)
        if self.rgb_matrix:
            bufs["rgb_matrix"] = bytes(self.rgb_matrix)
        if self.macros_raw:
            bufs["macro_table"] = bytes(self.macros_raw)
        if self.dks_raw:
            bufs["dks_table"] = bytes(self.dks_raw)
        if self.game_mode:
            bufs["game_mode"] = self.game_mode.to_payload()
        return bufs

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "name": self.name or f"Profile {self.profile_id}",
            "hall_raw_hex": self.hall.to_bytes().hex() if self.hall else None,
            "remap_raw_hex": self.remap.raw_bytes.hex() if self.remap else None,
            "remap_l2_raw_hex": self.remap_l2.raw_bytes.hex() if self.remap_l2 else None,
            "rgb_global": self.rgb_global.to_dict() if self.rgb_global else None,
            "rgb_matrix_hex": self.rgb_matrix.hex() if self.rgb_matrix else None,
            "macros_raw_hex": self.macros_raw.hex() if self.macros_raw else None,
            "dks_raw_hex": self.dks_raw.hex() if self.dks_raw else None,
            "game_mode_hex": self.game_mode.to_payload().hex() if self.game_mode else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Profile:
        pid = data.get("profile_id", 1)
        name = data.get("name", f"Profile {pid}")

        hall = None
        hall_hex = data.get("hall_raw_hex") or data.get("hall_p1_hex")
        if hall_hex:
            hall = HallProfileState.from_bytes(bytes.fromhex(hall_hex), profile_id=pid)

        remap = None
        remap_hex = data.get("remap_raw_hex") or data.get("remap_l1_hex")
        if remap_hex:
            raw = bytes.fromhex(remap_hex)
            slots = {
                s: KeyRemapRecord.from_bytes(raw[s * 4 : (s + 1) * 4])
                for s in range(KEYMAP_SLOT_COUNT)
            }
            remap = KeymapTable(layer=1, slots=slots, raw_bytes=raw)

        remap_l2 = None
        remap_l2_hex = data.get("remap_l2_raw_hex") or data.get("remap_l2_hex")
        if remap_l2_hex:
            raw2 = bytes.fromhex(remap_l2_hex)
            slots2 = {
                s: KeyRemapRecord.from_bytes(raw2[s * 4 : (s + 1) * 4])
                for s in range(KEYMAP_SLOT_COUNT)
            }
            remap_l2 = KeymapTable(layer=2, slots=slots2, raw_bytes=raw2)

        rgb_global = None
        if data.get("rgb_global"):
            rgb_global = RGBGlobalConfig.from_dict(data["rgb_global"])

        rgb_matrix = bytes.fromhex(data["rgb_matrix_hex"]) if data.get("rgb_matrix_hex") else None
        macros_raw = bytes.fromhex(data["macros_raw_hex"]) if data.get("macros_raw_hex") else None

        dks_raw = None
        if data.get("dks_raw_hex"):
            dks_raw = bytes.fromhex(data["dks_raw_hex"])
        elif data.get("hall_profile_2_hex"):
            legacy = bytes.fromhex(data["hall_profile_2_hex"])
            dks_raw = legacy + bytes(16) if len(legacy) == 1008 else legacy

        game_mode = None
        if data.get("game_mode_hex"):
            raw_gm = bytes.fromhex(data["game_mode_hex"])
            synth = bytearray(REPORT_SIZE)
            synth[0] = 0x55
            synth[1] = 0x11
            synth[2] = 0x38
            synth[8 : 8 + len(raw_gm)] = raw_gm
            game_mode = parse_game_mode_response(synth)

        return cls(
            profile_id=pid,
            name=name,
            hall=hall,
            remap=remap,
            remap_l2=remap_l2,
            rgb_global=rgb_global,
            rgb_matrix=rgb_matrix,
            macros_raw=macros_raw,
            dks_raw=dks_raw,
            game_mode=game_mode,
            metadata=dict(data.get("metadata", {})),
        )

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> Profile:
        return cls.from_dict(json.loads(json_str))

    def save_json(self, file_path: Union[str, Path], indent: int = 2) -> None:
        Path(file_path).write_text(self.to_json(indent=indent), encoding="utf-8")

    @classmethod
    def load_json(cls, file_path: Union[str, Path]) -> Profile:
        return cls.from_json(Path(file_path).read_text(encoding="utf-8"))

    @classmethod
    def from_device_state(cls, state: DeviceState, profile_id: int = 1, name: str = "") -> Profile:
        return state.create_profile(profile_id=profile_id, name=name)


# Backwards compatibility alias
KeyboardProfile = Profile


@dataclass
class DeviceState:
    """
    Unified DeviceState for IO by Red Square Type 84 Magnetic Black.

    Integrates all confirmed, observed, and raw subsystems:
    1. Device Info (AA 10, 64B) -> DeviceInfoResponse
    2. GameMode / Performance Settings (AA 11, 64B) -> GameModeResponse
    3. Hall Profile 1 Switch Thresholds (AA 17, 1008B) -> HallProfileState
    4. DKS Table (AA 18, 1024B) -> raw bytes (64 records * 16B)
    5. Key Remap Layer 1 Base (AA 12, 512B) -> KeymapTable
    6. Key Remap Layer 2 Fn (AA 16, 512B) -> KeymapTable
    7. Layer 3 raw table (AA 1C, 512B) -> raw bytes (UNKNOWN firmware semantics)
    8. Global RGB config (AA 13, 64B) -> RGBGlobalConfig
    9. RGB Matrix / Per-Key buffer (AA 14, 512B) -> raw bytes
    10. Macro raw table (AA 15, 400B) -> raw bytes
    """
    device_info: Optional[DeviceInfoResponse] = None
    game_mode: Optional[GameModeResponse] = None
    active_profile_id: int = 1  # Host-side profile selection (not a hardware register)
    profiles: Dict[int, Profile] = field(default_factory=dict)

    # Layer and subsystem stores (byte-exact raw representations + structured views)
    remap_l1: Optional[KeymapTable] = None
    remap_l2: Optional[KeymapTable] = None
    remap_l3_raw: Optional[bytes] = None
    macro_table_raw: Optional[bytes] = None
    dks_table_raw: Optional[bytes] = None  # 1024-byte DKS table (AA 18)
    rgb_global: Optional[RGBGlobalConfig] = None
    rgb_matrix_raw: Optional[bytes] = None

    hall_profile_1: Optional[HallProfileState] = None

    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> Optional[GameModeResponse]:
        """Deprecated alias for game_mode (backwards compatibility)."""
        return self.game_mode

    @status.setter
    def status(self, val: Optional[GameModeResponse]) -> None:
        self.game_mode = val

    @property
    def active_profile(self) -> Profile:
        """Return the active Profile object."""
        if self.active_profile_id not in self.profiles:
            raise KeyError(f"Active profile #{self.active_profile_id} not loaded in DeviceState")
        return self.profiles[self.active_profile_id]

    @property
    def fn_layer(self) -> Optional[KeymapTable]:
        """Alias for remap_l2 (backwards compatibility)."""
        return self.remap_l2

    @property
    def alt_layer(self) -> Optional[KeymapTable]:
        """Parsed view of remap_l3_raw if available."""
        if self.remap_l3_raw and len(self.remap_l3_raw) == KEYMAP_BUFFER_SIZE:
            slots: Dict[int, KeyRemapRecord] = {}
            for s in range(KEYMAP_SLOT_COUNT):
                off = s * KEYMAP_RECORD_SIZE
                slots[s] = KeyRemapRecord.from_bytes(self.remap_l3_raw[off : off + KEYMAP_RECORD_SIZE])
            return KeymapTable(layer=3, slots=slots, raw_bytes=bytes(self.remap_l3_raw))
        return None

    @property
    def dks_table(self) -> Optional[Any]:
        """Parsed DKSTable view of dks_table_raw if 1024 bytes."""
        if self.dks_table_raw and len(self.dks_table_raw) == 1024:
            from keyboard_re.models.dks import DKSTable
            return DKSTable.from_bytes(self.dks_table_raw)
        return None

    @property
    def hall_profile(self) -> Optional[HallProfileState]:
        """Alias for hall_profile_1."""
        return self.hall_profile_1

    @property
    def hall_profile_2(self) -> Optional[HallProfileState]:
        """
        Deprecated: AA 18 is GET_MAGNETIC_AXIS_DKS_DATA, not a second Hall profile.
        Always returns None in the corrected architecture.
        """
        return None

    def get_profile(self, profile_id: int) -> Profile:
        """Get profile by ID."""
        if profile_id not in self.profiles:
            raise KeyError(f"Profile #{profile_id} not found in DeviceState")
        return self.profiles[profile_id]

    def create_profile(self, profile_id: int = 1, name: str = "") -> Profile:
        """
        Create a new standalone Profile from current DeviceState subsystems.
        Clones all data to ensure isolation.
        """
        if self.hall_profile_1 is None:
            raise ValueError("Cannot create Profile: DeviceState has no Hall profile data")
        if self.remap_l1 is None:
            raise ValueError("Cannot create Profile: DeviceState has no Remap Layer 1 data")

        return Profile(
            profile_id=profile_id,
            name=name or f"Profile {profile_id}",
            hall=self.hall_profile_1.clone(),
            remap=KeymapTable(
                layer=1,
                slots=dict(self.remap_l1.slots),
                raw_bytes=self.remap_l1.raw_bytes,
            ),
            remap_l2=KeymapTable(
                layer=2,
                slots=dict(self.remap_l2.slots),
                raw_bytes=self.remap_l2.raw_bytes,
            ) if self.remap_l2 else None,
            rgb_global=copy.deepcopy(self.rgb_global) if self.rgb_global else None,
            rgb_matrix=bytes(self.rgb_matrix_raw) if self.rgb_matrix_raw else None,
            macros_raw=bytes(self.macro_table_raw) if self.macro_table_raw else None,
            dks_raw=bytes(self.dks_table_raw) if self.dks_table_raw else None,
            game_mode=self.game_mode.clone() if self.game_mode else None,
        )

    def clone(self) -> DeviceState:
        return DeviceState(
            device_info=copy.deepcopy(self.device_info),
            game_mode=copy.deepcopy(self.game_mode),
            active_profile_id=self.active_profile_id,
            profiles={pid: p.clone() for pid, p in self.profiles.items()},
            remap_l1=copy.deepcopy(self.remap_l1),
            remap_l2=copy.deepcopy(self.remap_l2),
            remap_l3_raw=bytes(self.remap_l3_raw) if self.remap_l3_raw else None,
            macro_table_raw=bytes(self.macro_table_raw) if self.macro_table_raw else None,
            dks_table_raw=bytes(self.dks_table_raw) if self.dks_table_raw else None,
            rgb_global=copy.deepcopy(self.rgb_global),
            rgb_matrix_raw=bytes(self.rgb_matrix_raw) if self.rgb_matrix_raw else None,
            hall_profile_1=self.hall_profile_1.clone() if self.hall_profile_1 else None,
            metadata=dict(self.metadata),
        )

    def clone_mutable(self) -> DeviceState:
        return self.clone()

    def to_snapshot(self) -> KeyboardSnapshot:
        """Export to immutable snapshot for diffing and transaction verification."""
        return KeyboardSnapshot(
            device_info=self.device_info,
            game_mode=self.game_mode,
            active_profile_id=self.active_profile_id,
            profiles={pid: p.clone() for pid, p in self.profiles.items()},
            fn_layer=copy.deepcopy(self.remap_l2),
            alt_layer=self.alt_layer,
            metadata=dict(self.metadata),
            dks_table_raw=bytes(self.dks_table_raw) if self.dks_table_raw else None,
        )

    def dump_raw_buffers(self) -> Dict[str, bytes]:
        """
        Export all available raw subsystem buffers.
        Guarantees 100% byte-exact binary preservation across round-trips.
        """
        bufs: Dict[str, bytes] = {}
        if self.device_info:
            bufs["device_info"] = self.device_info.raw_header
        if self.game_mode:
            bufs["game_mode"] = self.game_mode.raw_payload
        if self.remap_l1:
            bufs["remap_l1"] = self.remap_l1.raw_bytes
        if self.remap_l2:
            bufs["remap_l2"] = self.remap_l2.raw_bytes
        if self.remap_l3_raw:
            bufs["remap_l3"] = bytes(self.remap_l3_raw)
        if self.rgb_global:
            if self.rgb_global.raw_payload and len(self.rgb_global.raw_payload) == REPORT_SIZE:
                bufs["rgb_global"] = bytes(self.rgb_global.raw_payload)
            else:
                raw_rgb = bytearray(REPORT_SIZE)
                raw_rgb[0] = 0x55
                raw_rgb[1] = 0x13
                raw_rgb[2] = 0x10
                raw_rgb[3:8] = self.rgb_global.reserved_header
                raw_rgb[8] = self.rgb_global.effect & 0xFF
                raw_rgb[9:12] = bytes(self.rgb_global.primary)
                raw_rgb[12] = self.rgb_global.driver_setting & 0xFF
                raw_rgb[13:16] = bytes(self.rgb_global.secondary)
                raw_rgb[16] = self.rgb_global.color_mode & 0xFF
                raw_rgb[17] = self.rgb_global.brightness & 0xFF
                raw_rgb[18] = self.rgb_global.speed & 0xFF
                raw_rgb[19] = self.rgb_global.direction & 0xFF
                raw_rgb[20] = self.rgb_global.effect_mode_type & 0xFF
                raw_rgb[21] = self.rgb_global.reserved_byte21 & 0xFF
                raw_rgb[22:24] = self.rgb_global.magic
                bufs["rgb_global"] = bytes(raw_rgb)
        if self.rgb_matrix_raw:
            bufs["rgb_matrix"] = bytes(self.rgb_matrix_raw)
        if self.macro_table_raw:
            bufs["macro_table"] = bytes(self.macro_table_raw)
        if self.dks_table_raw:
            bufs["dks_table"] = bytes(self.dks_table_raw)
        if self.hall_profile_1:
            bufs["hall_p1"] = self.hall_profile_1.to_bytes()
        return bufs

    @classmethod
    def from_raw_buffers(cls, buffers: Dict[str, bytes]) -> DeviceState:
        """
        Construct DeviceState from a collection of raw binary subsystem buffers.
        """
        device_info = None
        if "device_info" in buffers:
            raw_info = buffers["device_info"]
            if len(raw_info) == REPORT_SIZE:
                device_info = parse_handshake_response(raw_info)

        game_mode_resp = None
        raw_gm = buffers.get("game_mode") or buffers.get("status")
        if raw_gm is not None:
            if len(raw_gm) == REPORT_SIZE:
                game_mode_resp = parse_game_mode_response(raw_gm)
            elif len(raw_gm) == 16:
                synthetic_rep = bytearray(REPORT_SIZE)
                synthetic_rep[0] = 0x55
                synthetic_rep[1] = 0x11
                synthetic_rep[2] = 0x38
                synthetic_rep[8:24] = raw_gm
                game_mode_resp = parse_game_mode_response(synthetic_rep)

        # Remap layers
        remap_l1 = None
        if "remap_l1" in buffers and len(buffers["remap_l1"]) == KEYMAP_BUFFER_SIZE:
            buf = buffers["remap_l1"]
            slots = {
                s: KeyRemapRecord.from_bytes(buf[s * 4 : (s + 1) * 4])
                for s in range(KEYMAP_SLOT_COUNT)
            }
            remap_l1 = KeymapTable(layer=1, slots=slots, raw_bytes=buf)

        remap_l2 = None
        if "remap_l2" in buffers and len(buffers["remap_l2"]) == KEYMAP_BUFFER_SIZE:
            buf = buffers["remap_l2"]
            slots = {
                s: KeyRemapRecord.from_bytes(buf[s * 4 : (s + 1) * 4])
                for s in range(KEYMAP_SLOT_COUNT)
            }
            remap_l2 = KeymapTable(layer=2, slots=slots, raw_bytes=buf)

        remap_l3_raw = buffers.get("remap_l3")

        rgb_global = None
        if "rgb_global" in buffers and len(buffers["rgb_global"]) == REPORT_SIZE:
            rgb_global = parse_rgb_global_read_response(buffers["rgb_global"])

        rgb_matrix_raw = buffers.get("rgb_matrix")
        macro_table_raw = buffers.get("macro_table")

        dks_table_raw = buffers.get("dks_table")
        if dks_table_raw is None and "hall_p2" in buffers:
            # Legacy migration fallback: pad 1008 bytes to 1024 bytes if needed
            legacy_p2 = buffers["hall_p2"]
            if len(legacy_p2) == 1008:
                dks_table_raw = legacy_p2 + bytes(16)
            else:
                dks_table_raw = legacy_p2

        hall_p1 = None
        if "hall_p1" in buffers and len(buffers["hall_p1"]) >= CONFIG_IMAGE_SIZE:
            hall_p1 = HallProfileState.from_bytes(buffers["hall_p1"], profile_id=1)

        # Build Profile objects (software-defined profiles)
        profiles: Dict[int, Profile] = {}
        if hall_p1 and remap_l1:
            profiles[1] = Profile(
                profile_id=1,
                name="Default",
                hall=hall_p1,
                remap=remap_l1,
                remap_l2=remap_l2,
                rgb_global=rgb_global,
                rgb_matrix=rgb_matrix_raw,
                macros_raw=macro_table_raw,
                dks_raw=dks_table_raw,
            )

        return cls(
            device_info=device_info,
            game_mode=game_mode_resp,
            active_profile_id=1,  # Host-side selection
            profiles=profiles,
            remap_l1=remap_l1,
            remap_l2=remap_l2,
            remap_l3_raw=remap_l3_raw,
            macro_table_raw=macro_table_raw,
            dks_table_raw=dks_table_raw,
            rgb_global=rgb_global,
            rgb_matrix_raw=rgb_matrix_raw,
            hall_profile_1=hall_p1,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize state to a JSON-serializable structured dictionary."""
        return {
            "device_info": {
                "vid": f"0x{self.device_info.vid:04X}",
                "pid": f"0x{self.device_info.pid:04X}",
                "firmware_version": self.device_info.firmware_version,
                "bootloader_version": self.device_info.bootloader_version,
                "chip_id_hex": self.device_info.chip_id.hex(),
            } if self.device_info else None,
            "game_mode": {
                "sleep_time": self.game_mode.sleep_time,
                "report_rate": self.game_mode.report_rate,
                "stability_mode": self.game_mode.stability_mode,
                "auto_calibration": self.game_mode.auto_calibration,
                "raw_payload_hex": self.game_mode.raw_payload.hex(),
            } if self.game_mode else None,
            "status": {
                "active_profile": self.game_mode.sleep_time,
                "lock_flags": self.game_mode.report_rate,
                "mode_flag": self.game_mode.stability_mode,
                "connection_flag": self.game_mode.auto_calibration,
                "raw_payload_hex": self.game_mode.raw_payload.hex(),
            } if self.game_mode else None,
            "active_profile_id": self.active_profile_id,
            "profiles": {pid: p.to_dict() for pid, p in self.profiles.items()},
            "remap_l1_hex": self.remap_l1.raw_bytes.hex() if self.remap_l1 else None,
            "remap_l2_hex": self.remap_l2.raw_bytes.hex() if self.remap_l2 else None,
            "remap_l3_raw_hex": self.remap_l3_raw.hex() if self.remap_l3_raw else None,
            "macro_table_raw_hex": self.macro_table_raw.hex() if self.macro_table_raw else None,
            "dks_table_raw_hex": self.dks_table_raw.hex() if self.dks_table_raw else None,
            "rgb_global": self.rgb_global.to_dict() if self.rgb_global else None,
            "rgb_global_hex": self.dump_raw_buffers().get("rgb_global").hex() if self.rgb_global else None,
            "rgb_matrix_raw_hex": self.rgb_matrix_raw.hex() if self.rgb_matrix_raw else None,
            "hall_profile_1_hex": self.hall_profile_1.to_bytes().hex() if self.hall_profile_1 else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DeviceState:
        """Reconstruct DeviceState from dictionary."""
        buffers: Dict[str, bytes] = {}
        if data.get("remap_l1_hex"):
            buffers["remap_l1"] = bytes.fromhex(data["remap_l1_hex"])
        if data.get("remap_l2_hex"):
            buffers["remap_l2"] = bytes.fromhex(data["remap_l2_hex"])
        if data.get("remap_l3_raw_hex"):
            buffers["remap_l3"] = bytes.fromhex(data["remap_l3_raw_hex"])
        if data.get("macro_table_raw_hex"):
            buffers["macro_table"] = bytes.fromhex(data["macro_table_raw_hex"])
        if data.get("dks_table_raw_hex"):
            buffers["dks_table"] = bytes.fromhex(data["dks_table_raw_hex"])
        elif data.get("hall_profile_2_hex"):
            buffers["dks_table"] = bytes.fromhex(data["hall_profile_2_hex"])
        if data.get("rgb_global_hex"):
            buffers["rgb_global"] = bytes.fromhex(data["rgb_global_hex"])
        elif data.get("rgb_global"):
            rg = data["rgb_global"]
            rgb_cfg = RGBGlobalConfig.from_dict(rg)
            raw_rgb = bytearray(REPORT_SIZE)
            raw_rgb[0] = 0x55
            raw_rgb[1] = 0x13
            raw_rgb[2] = 0x10
            raw_rgb[3:8] = rgb_cfg.reserved_header
            raw_rgb[8] = rgb_cfg.effect & 0xFF
            raw_rgb[9:12] = bytes(rgb_cfg.primary)
            raw_rgb[12] = rgb_cfg.driver_setting & 0xFF
            raw_rgb[13:16] = bytes(rgb_cfg.secondary)
            raw_rgb[16] = rgb_cfg.color_mode & 0xFF
            raw_rgb[17] = rgb_cfg.brightness & 0xFF
            raw_rgb[18] = rgb_cfg.speed & 0xFF
            raw_rgb[19] = rgb_cfg.direction & 0xFF
            raw_rgb[20] = rgb_cfg.effect_mode_type & 0xFF
            raw_rgb[21] = rgb_cfg.reserved_byte21 & 0xFF
            raw_rgb[22:24] = rgb_cfg.magic
            buffers["rgb_global"] = bytes(raw_rgb)
        if data.get("rgb_matrix_raw_hex"):
            buffers["rgb_matrix"] = bytes.fromhex(data["rgb_matrix_raw_hex"])
        if data.get("hall_profile_1_hex"):
            buffers["hall_p1"] = bytes.fromhex(data["hall_profile_1_hex"])

        state = cls.from_raw_buffers(buffers)
        state.active_profile_id = data.get("active_profile_id", 1)
        state.metadata = dict(data.get("metadata", {}))

        # Reconstruct structured profiles if present in dictionary
        if "profiles" in data and isinstance(data["profiles"], dict) and data["profiles"]:
            for pid_str, p_dict in data["profiles"].items():
                try:
                    pid = int(pid_str)
                    state.profiles[pid] = Profile.from_dict(p_dict)
                except Exception:
                    pass

        # Reconstruct structured device_info if present
        d_info = data.get("device_info")
        if d_info:
            vid = int(d_info["vid"], 16) if isinstance(d_info["vid"], str) else d_info["vid"]
            pid = int(d_info["pid"], 16) if isinstance(d_info["pid"], str) else d_info["pid"]
            state.device_info = DeviceInfoResponse(
                raw_header=bytes(64),
                chip_id=bytes.fromhex(d_info.get("chip_id_hex", "")),
                vid=vid,
                pid=pid,
                firmware_version=d_info.get("firmware_version", "1.00"),
                bootloader_version=d_info.get("bootloader_version", "1.00"),
            )

        d_gm = data.get("game_mode")
        if d_gm:
            payload = bytes.fromhex(d_gm.get("raw_payload_hex", ""))
            state.game_mode = GameModeResponse(
                sleep_time=d_gm.get("sleep_time", 1),
                report_rate=d_gm.get("report_rate", 6),
                stability_mode=d_gm.get("stability_mode", 1),
                auto_calibration=d_gm.get("auto_calibration", 1),
                raw_payload=payload,
            )
        else:
            d_status = data.get("status")
            if d_status:
                payload = bytes.fromhex(d_status.get("raw_payload_hex", ""))
                state.game_mode = GameModeResponse(
                    sleep_time=d_status.get("active_profile", 1),
                    report_rate=d_status.get("lock_flags", 6),
                    stability_mode=d_status.get("mode_flag", 1),
                    auto_calibration=d_status.get("connection_flag", 1),
                    raw_payload=payload,
                )

        return state

    def to_json(self, indent: int = 2) -> str:
        """Serialize state to formatted JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> DeviceState:
        """Deserialize state from JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)

    def save_json(self, file_path: Union[str, Path], indent: int = 2) -> None:
        """Save state to JSON file."""
        Path(file_path).write_text(self.to_json(indent=indent), encoding="utf-8")

    @classmethod
    def load_json(cls, file_path: Union[str, Path]) -> DeviceState:
        """Load state from file (supports both exported state JSON and WebHID capture JSON)."""
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "events" in data:
            state = cls.from_capture_events(data["events"])
            state.metadata["source_file"] = str(file_path)
            state.metadata["captured_at"] = data.get("captured_at")
            state.metadata["description"] = data.get("description")
            return state

        return cls.from_dict(data)

    @classmethod
    def from_capture_events(cls, events: Sequence[Dict[str, Any]]) -> DeviceState:
        """
        Parse raw bidirectional WebHID events (e.g. from read_01_initial_load.json)
        into a complete structured DeviceState.
        """
        reports_by_opcode: Dict[str, List[bytes]] = {}

        for e in events:
            if e.get("direction") == "DEVICE -> HOST":
                hex_data = e.get("data_hex", "")
                if hex_data.startswith("55") and len(hex_data) >= 4:
                    op = hex_data[:4].lower()
                    raw = bytes.fromhex(hex_data)
                    reports_by_opcode.setdefault(op, []).append(raw)

        # 1. Device Info (55 10)
        device_info: Optional[DeviceInfoResponse] = None
        if "5510" in reports_by_opcode and len(reports_by_opcode["5510"]) > 0:
            device_info = parse_handshake_response(reports_by_opcode["5510"][0])

        # 2. Game Mode / Performance (55 11)
        game_mode_resp: Optional[GameModeResponse] = None
        if "5511" in reports_by_opcode and len(reports_by_opcode["5511"]) > 0:
            game_mode_resp = parse_game_mode_response(reports_by_opcode["5511"][0])

        # 3. Keymap Layer 1 (55 12)
        keymap_l1: Optional[KeymapTable] = None
        if "5512" in reports_by_opcode and len(reports_by_opcode["5512"]) == 10:
            keymap_l1 = parse_keymap_chunks(reports_by_opcode["5512"], expected_opcode=0x12, layer=1)

        # 4. Fn Layer (55 16)
        keymap_l2: Optional[KeymapTable] = None
        if "5516" in reports_by_opcode and len(reports_by_opcode["5516"]) == 10:
            keymap_l2 = parse_keymap_chunks(reports_by_opcode["5516"], expected_opcode=0x16, layer=2)

        # 5. Layer 3 raw table / UNKNOWN semantics (55 1C)
        remap_l3_raw: Optional[bytes] = None
        if "551c" in reports_by_opcode and len(reports_by_opcode["551c"]) == 10:
            table_l3 = parse_calibration_response(reports_by_opcode["551c"])
            remap_l3_raw = table_l3.raw_bytes

        # 6. Global RGB (55 13)
        rgb_global: Optional[RGBGlobalConfig] = None
        if "5513" in reports_by_opcode and len(reports_by_opcode["5513"]) > 0:
            rgb_global = parse_rgb_global_read_response(reports_by_opcode["5513"][0])

        # 7. Per-Key RGB (55 14)
        rgb_matrix_raw: Optional[bytes] = None
        if "5514" in reports_by_opcode and len(reports_by_opcode["5514"]) == 10:
            rgb_matrix_raw = parse_rgb_per_key_read_chunks(reports_by_opcode["5514"])

        # 8. Macros (55 15)
        macro_table_raw: Optional[bytes] = None
        if "5515" in reports_by_opcode and len(reports_by_opcode["5515"]) == 8:
            macro_tbl = parse_macro_chunks(reports_by_opcode["5515"])
            macro_table_raw = macro_tbl.raw_bytes

        # 9. Hall Profile 1 (55 17)
        hall_p1: Optional[HallProfileState] = None
        if "5517" in reports_by_opcode and len(reports_by_opcode["5517"]) in (18, 19):
            img_p1 = parse_hall_read_chunks(reports_by_opcode["5517"])
            hall_p1 = HallProfileState.from_bytes(profile_id=1, image_bytes=img_p1)

        # 10. DKS Table (55 18)
        dks_table_raw: Optional[bytes] = None
        if "5518" in reports_by_opcode and len(reports_by_opcode["5518"]) in (18, 19):
            dks_table_raw = parse_dks_read_chunks(reports_by_opcode["5518"])

        # Assemble profiles (Profile 1 is active hardware state)
        profiles: Dict[int, Profile] = {}
        if hall_p1 is not None and keymap_l1 is not None:
            profiles[1] = Profile(
                profile_id=1,
                hall=hall_p1,
                remap=keymap_l1,
                rgb_global=rgb_global,
                rgb_matrix=rgb_matrix_raw,
                macros_raw=macro_table_raw,
                dks_raw=dks_table_raw,
            )

        return cls(
            device_info=device_info,
            game_mode=game_mode_resp,
            active_profile_id=1,  # Host-side selection
            profiles=profiles,
            remap_l1=keymap_l1,
            remap_l2=keymap_l2,
            remap_l3_raw=remap_l3_raw,
            macro_table_raw=macro_table_raw,
            dks_table_raw=dks_table_raw,
            rgb_global=rgb_global,
            rgb_matrix_raw=rgb_matrix_raw,
            hall_profile_1=hall_p1,
        )


# Backwards compatibility alias
KeyboardState = DeviceState


@dataclass(frozen=True)
class KeyboardSnapshot:
    """
    Immutable-ish point-in-time snapshot representing keyboard configuration.
    Supports structured diffing against other snapshots.
    """
    device_info: Optional[DeviceInfoResponse]
    game_mode: Optional[GameModeResponse]
    active_profile_id: int
    profiles: Dict[int, Profile]
    fn_layer: Optional[KeymapTable]
    alt_layer: Optional[KeymapTable]
    metadata: Dict[str, Any] = field(default_factory=dict)
    dks_table_raw: Optional[bytes] = None

    @property
    def status(self) -> Optional[GameModeResponse]:
        """Deprecated alias for game_mode."""
        return self.game_mode

    @property
    def active_profile(self) -> Profile:
        if self.active_profile_id not in self.profiles:
            raise KeyError(f"Active profile #{self.active_profile_id} not loaded in snapshot")
        return self.profiles[self.active_profile_id]

    def clone_mutable(self) -> DeviceState:
        remap_l1 = self.profiles[1].remap if 1 in self.profiles else None
        hall_p1 = self.profiles[1].hall if 1 in self.profiles else None
        rgb_global = self.profiles[1].rgb_global if 1 in self.profiles else None
        rgb_matrix = self.profiles[1].rgb_matrix if 1 in self.profiles else None
        macros_raw = self.profiles[1].macros_raw if 1 in self.profiles else None

        return DeviceState(
            device_info=copy.deepcopy(self.device_info),
            game_mode=copy.deepcopy(self.game_mode),
            active_profile_id=self.active_profile_id,
            profiles={pid: p.clone() for pid, p in self.profiles.items()},
            remap_l1=copy.deepcopy(remap_l1),
            remap_l2=copy.deepcopy(self.fn_layer),
            remap_l3_raw=bytes(self.alt_layer.raw_bytes) if self.alt_layer else None,
            macro_table_raw=macros_raw,
            dks_table_raw=bytes(self.dks_table_raw) if self.dks_table_raw else None,
            rgb_global=copy.deepcopy(rgb_global),
            rgb_matrix_raw=rgb_matrix,
            hall_profile_1=hall_p1.clone() if hall_p1 else None,
            metadata=dict(self.metadata),
        )

    def diff(self, other: KeyboardSnapshot) -> List[StateDiff]:
        """Compute structured difference between this snapshot and another."""
        from keyboard_re.protocol.diff import compare_snapshots
        return compare_snapshots(self, other)

    @classmethod
    def load_json(cls, file_path: Union[str, Path]) -> KeyboardSnapshot:
        state = DeviceState.load_json(file_path)
        return state.to_snapshot()


def collect_device_state(
    transport: HidTransport,
    timeout: float = 1.0,
) -> DeviceState:
    """
    Read-only collection of complete keyboard DeviceState across all 10 subsystems.
    
    Queries (HOST -> DEVICE):
    1. AA 10: Handshake / Device Identity
    2. AA 11: Performance Settings (GameModeResponse)
    3. AA 12: Key Remap Layer 1 (Base layout, 512B)
    4. AA 16: Key Remap Layer 2 (Fn layout, 512B)
    5. AA 1C: Layer 3 raw table (UNKNOWN semantics, 512B)
    6. AA 13: Global RGB Configuration (64B)
    7. AA 14: Per-Key RGB Matrix buffer (512B)
    8. AA 15: Macro table buffer (400B)
    9. AA 17: Hall Switch Configuration Profile 1 (1008B)
    10. AA 18: DKS Configuration Table (1024B)

    Safety:
    - 100% Read-Only: performs zero write operations.
    """
    # 1. Device Info (AA 10)
    dev_info = read_device_info(transport, timeout=timeout)

    # 2. Game Mode (AA 11)
    game_mode_resp = read_game_mode(transport, timeout=timeout)

    # 3. Remap Layer 1 (AA 12)
    l1_bytes = read_keymap_table(transport, opcode=0x12, timeout=timeout)
    slots_l1 = {
        s: KeyRemapRecord.from_bytes(l1_bytes[s * 4 : (s + 1) * 4])
        for s in range(KEYMAP_SLOT_COUNT)
    }
    remap_l1 = KeymapTable(layer=1, slots=slots_l1, raw_bytes=l1_bytes)

    # 4. Remap Layer 2 (AA 16)
    l2_bytes = read_keymap_table(transport, opcode=0x16, timeout=timeout)
    slots_l2 = {
        s: KeyRemapRecord.from_bytes(l2_bytes[s * 4 : (s + 1) * 4])
        for s in range(KEYMAP_SLOT_COUNT)
    }
    remap_l2 = KeymapTable(layer=2, slots=slots_l2, raw_bytes=l2_bytes)

    # 5. Layer 3 raw table (AA 1C)
    remap_l3_raw = read_keymap_table(transport, opcode=0x1C, timeout=timeout)

    # 6. Global RGB (AA 13)
    rgb_global = read_rgb_global(transport, timeout=timeout)

    # 7. Per-Key RGB buffer (AA 14)
    rgb_matrix_raw = read_rgb_per_key(transport, timeout=timeout)

    # 8. Macro buffer (AA 15)
    macro_table_raw = read_macro_table(transport, timeout=timeout)

    # 9. Hall Profile 1 (AA 17)
    hall_p1_bytes = read_hall_profile(transport, profile_id=1, timeout=timeout)
    hall_p1 = HallProfileState.from_bytes(hall_p1_bytes, profile_id=1)

    # 10. DKS Table (AA 18)
    dks_table_raw = read_dks_table(transport, timeout=timeout)

    # Assemble Profile objects (Profile 1 is active hardware state)
    profiles: Dict[int, Profile] = {
        1: Profile(
            profile_id=1,
            hall=hall_p1,
            remap=remap_l1,
            rgb_global=rgb_global,
            rgb_matrix=rgb_matrix_raw,
            macros_raw=macro_table_raw,
            dks_raw=dks_table_raw,
        ),
    }

    return DeviceState(
        device_info=dev_info,
        game_mode=game_mode_resp,
        active_profile_id=1,  # Host-side selection
        profiles=profiles,
        remap_l1=remap_l1,
        remap_l2=remap_l2,
        remap_l3_raw=remap_l3_raw,
        macro_table_raw=macro_table_raw,
        dks_table_raw=dks_table_raw,
        rgb_global=rgb_global,
        rgb_matrix_raw=rgb_matrix_raw,
        hall_profile_1=hall_p1,
        metadata={"collected_via": "collect_device_state"},
    )
