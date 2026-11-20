"""Build the auditable native guard with an explicitly supplied Zig executable.

No compiler is downloaded by this script. Runtime installations need no compiler.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--zig", required=True)
    ap.add_argument("--target", choices=("x86_64-windows-gnu", "aarch64-windows-gnu"), default="x86_64-windows-gnu")
    args = ap.parse_args()
    out = ROOT / "pc_control" / "native" / ("focus_guard_x64.dll" if args.target.startswith("x86_64") else "focus_guard_arm64.dll")
    out.parent.mkdir(exist_ok=True)
    subprocess.run([args.zig, "cc", "-target", args.target, "-Os", "-shared",
                    str(ROOT / "native" / "focus_guard.c"), "-o", str(out), "-luser32", "-lkernel32"], check=True)
    manifest_path = out.parent / "focus_guard_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest[out.name] = {"source_sha256": hashlib.sha256((ROOT / "native" / "focus_guard.c").read_bytes()).hexdigest(),
                          "binary_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
                          "compiler": subprocess.check_output([args.zig, "version"], text=True).strip()}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Focus guard built:", out.name)
