"""Pytest configuration and environment initialization."""

import os
import sys

# Ensure Tkinter can find Tcl/Tk libraries on Windows when running under Python 3.14
if sys.platform == "win32":
    tcl_candidate = r"C:\Python314\tcl\tcl8.6"
    tk_candidate = r"C:\Python314\tcl\tk8.6"
    if os.path.isdir(tcl_candidate) and "TCL_LIBRARY" not in os.environ:
        os.environ["TCL_LIBRARY"] = tcl_candidate
    if os.path.isdir(tk_candidate) and "TK_LIBRARY" not in os.environ:
        os.environ["TK_LIBRARY"] = tk_candidate
