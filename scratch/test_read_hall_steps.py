"""
Step-by-step diagnostic of AA 17 read.
"""
from pathlib import Path
import struct
import sys
import time

WORKSPACE_ROOT = Path(r"c:\KeyboardSoft")
sys.path.insert(0, str(WORKSPACE_ROOT / "src"))

from keyboard_re.transport.native_hid import NativeHidTransport

def main():
    t = NativeHidTransport()
    t.open()
    try:
        print("Connected.", flush=True)
        drained = t.drain_input_buffer(max_reports=1024)
        print(f"Drained {drained} reports.", flush=True)

        for i in range(18):
            addr = i * 56
            req = bytearray(64)
            req[0] = 0xAA
            req[1] = 0x17
            req[2] = 0x38
            struct.pack_into("<H", req, 3, addr)
            t.send_report(0, bytes(req))
            resp = t.receive_report(timeout=1.0, expected_opcode=0x17, expected_address=addr)
            resp_addr = struct.unpack_from("<H", resp, 3)[0]
            print(f"Chunk #{i}: addr 0x{addr:04X} -> got {resp[:8].hex(' ').upper()} (resp_addr=0x{resp_addr:04X})", flush=True)

        # Terminator
        req_term = bytearray(64)
        req_term[0] = 0xAA
        req_term[1] = 0x17
        req_term[2] = 0x10
        struct.pack_into("<H", req_term, 3, 0x03F0)
        req_term[5:8] = bytes([0x00, 0x01, 0x00])
        t.send_report(0, bytes(req_term))
        resp_term = t.receive_report(timeout=1.0, expected_opcode=0x17)
        print(f"Terminator -> got {resp_term[:8].hex(' ').upper()}", flush=True)
        print("All chunks + terminator read successfully!", flush=True)

    finally:
        t.close()

if __name__ == "__main__":
    main()
