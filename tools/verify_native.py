"""Verify source/binary freshness, PE architectures and wheel package data."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def verify(wheel=None):
    base = ROOT / "pc_control" / "native"
    manifest = json.loads((base / "focus_guard_manifest.json").read_text(encoding="utf-8"))
    source_hash = hashlib.sha256((ROOT / "native" / "focus_guard.c").read_bytes()).hexdigest()
    expected = {"focus_guard_x64.dll": 0x8664, "focus_guard_arm64.dll": 0xAA64}
    for name, machine in expected.items():
        data = (base / name).read_bytes()
        assert data[:2] == b"MZ", name
        offset = struct.unpack_from("<I", data, 60)[0]
        assert data[offset:offset+4] == b"PE\0\0", name
        assert struct.unpack_from("<H", data, offset+4)[0] == machine, name
        assert manifest[name]["source_sha256"] == source_hash, f"rebuild stale {name}"
        assert manifest[name]["binary_sha256"] == hashlib.sha256(data).hexdigest(), f"binary drift: {name}"
    if wheel:
        with zipfile.ZipFile(wheel) as archive:
            for name in (*expected, "focus_guard_manifest.json"):
                assert archive.read("pc_control/native/" + name) == (base / name).read_bytes(), name
            for module in (ROOT / "pc_control").glob("*.py"):
                assert archive.read("pc_control/" + module.name) == module.read_bytes(), f"stale module: {module.name}"
        print("Native wheel package verification passed")
    print("Native source/binary verification passed")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--wheel", type=Path)
    args = ap.parse_args()
    verify(args.wheel)
