"""
Hardware Transport Module for IO by Red Square Type 84 Magnetic Black.
"""

from keyboard_re.transport.native_hid import (
    NativeHidTransport,
    TARGET_PID,
    TARGET_PRODUCT_NAME,
    TARGET_USAGE,
    TARGET_USAGE_PAGE,
    TARGET_VID,
)

__all__ = [
    "NativeHidTransport",
    "TARGET_VID",
    "TARGET_PID",
    "TARGET_USAGE_PAGE",
    "TARGET_USAGE",
    "TARGET_PRODUCT_NAME",
]
