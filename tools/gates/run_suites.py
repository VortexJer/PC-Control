"""Run several existing test suites and require each one's own success token.

  python tools/gates/run_suites.py static steps server ...      (names = tests/test_<name>.py)
Each suite must exit 0 AND print "verification passed"; prints "suites verification passed" only if all of them do."""
import os, subprocess, sys, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
bad = []
for name in sys.argv[1:]:
    path = os.path.join(ROOT, "tests", f"test_{name}.py")
    t0 = time.time()
    p = subprocess.run([sys.executable, path], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONUTF8": "1"}, timeout=900)
    ok = p.returncode == 0 and "verification passed" in p.stdout
    print(f"{'ok  ' if ok else 'FAIL'} {name} ({time.time() - t0:.0f}s)")
    if not ok:
        bad.append(name)
        tail = (p.stdout + p.stderr).strip().splitlines()[-12:]
        print("     " + "\n     ".join(tail))
if not sys.argv[1:]:
    print("give suite names"); sys.exit(2)
if bad:
    print("FAILED: " + ", ".join(bad)); sys.exit(1)
print("suites verification passed")
