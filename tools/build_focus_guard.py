"""Build the auditable native guard with an explicitly supplied Zig executable.

No compiler is downloaded by this script. Runtime installations need no compiler.
x86 also builds focus_guard_host32.exe: Windows only installs a hook DLL from a process of the same architecture, so 32-bit apps
are guarded through that small 32-bit helper.
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
    ap.add_argument("--target", choices=("x86_64-windows-gnu", "aarch64-windows-gnu", "x86-windows-gnu"), default="x86_64-windows-gnu")
    args = ap.parse_args()
    zig = str(Path(args.zig).resolve())
    arch = {"x86_64": "x64", "aarch64": "arm64", "x86": "x86"}[args.target.split("-")[0]]
    native = ROOT / "pc_control" / "native"
    native.mkdir(exist_ok=True)
    builds = [("focus_guard.c", f"focus_guard_{arch}.dll", ["-shared"])]
    if arch == "x86":
        builds.append(("focus_guard_host32.c", "focus_guard_host32.exe", []))
    manifest_path = native / "focus_guard_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    for src, name, extra in builds:
        out = native / name
        subprocess.run([zig, "cc", "-target", args.target, "-Os", *extra, str(ROOT / "native" / src), "-o", str(out),
                        "-luser32", "-lkernel32"], check=True)
        if arch == "x86":
            for junk in (out.with_suffix(".pdb"), out.with_suffix(".lib")):
                junk.unlink(missing_ok=True)
        manifest[name] = {"source_sha256": hashlib.sha256((ROOT / "native" / src).read_bytes()).hexdigest(),
                          "binary_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
                          "compiler": subprocess.check_output([zig, "version"], text=True).strip()}
        print("Focus guard built:", name)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
