"""Pytest configuration and environment initialization."""

import os
import sys

# Ensure Tkinter can find Tcl/Tk libraries on Windows when running under Python 3.14
if sys.platform == "win32":
    tcl_candidate = os.path.normpath(os.path.join(sys.prefix, "tcl", "tcl8.6"))
    tk_candidate = os.path.normpath(os.path.join(sys.prefix, "tcl", "tk8.6"))
    if os.path.isdir(tcl_candidate):
        os.environ["TCL_LIBRARY"] = tcl_candidate
    if os.path.isdir(tk_candidate):
        os.environ["TK_LIBRARY"] = tk_candidate
