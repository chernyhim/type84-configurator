"""
IO by Red Square Type 84 Magnetic Black - Protocol Reverse Engineering Tools.

Passive analysis and inspection tools for HID output reports.
NO arbitrary HID packets or write commands are sent to the device.
"""

__version__ = "1.0.0-rc1"

from keyboard_re.applicator import (
    ApplyProfileResult,
    PlanConfirmationSummary,
    ProfileApplicator,
    apply_profile,
)
from keyboard_re.models.rgb_editor import RGBGlobalEditor
from keyboard_re.models.rgb_matrix import RGBMatrix
from keyboard_re.profile_manager import ProfileDiff, ProfileManager, SubsystemDiff
from keyboard_re.protocol.executor import (
    ExecutionStatus,
    PlanExecutionResult,
    ProfilePlanExecutor,
    execute_profile_write_plan,
)
from keyboard_re.protocol.plan import ProfileWritePlan, build_profile_write_plan

__all__ = [
    "__version__",
    "RGBGlobalEditor",
    "RGBMatrix",
    "ProfileManager",
    "ProfileDiff",
    "SubsystemDiff",
    "ProfileWritePlan",
    "build_profile_write_plan",
    "ExecutionStatus",
    "PlanExecutionResult",
    "ProfilePlanExecutor",
    "execute_profile_write_plan",
    "ApplyProfileResult",
    "PlanConfirmationSummary",
    "ProfileApplicator",
    "apply_profile",
]
