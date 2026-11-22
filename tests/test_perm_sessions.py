"""Two Claude sessions using PC-Control at once (each on its own app) do not clash over the shared permission files:
approving in one session keeps the other's approvals, nothing is read half-written, and concurrent writers lose nothing.
Positive control: the old approve() (rewrite with this session only) is shown to lose the other session's approval."""
import json, multiprocessing as mp, os, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="pcc_perm_")
os.environ["PC_CONTROL_HOME"] = HOME
from pc_control import perm


def as_session(sid, fn, *a):
    perm.session_id = lambda: sid
    return fn(*a)


def writer(sid, n, home):
    os.environ["PC_CONTROL_HOME"] = home
    from pc_control import perm as p
    p.session_id = lambda: sid
    for i in range(n):
        p.approve(f"{sid}-key-{i}")
        p.set_cc_mode("default" if i % 2 else "bypassPermissions")
        p.set_last("click", f"{sid}-{i}")


if __name__ == "__main__":
    checks = {}
    as_session("A", perm.approve, "terminal|cmd")
    as_session("B", perm.approve, "mail|outlook")
    checks["approving in B keeps A's approval"] = as_session("A", perm.approved, "terminal|cmd")
    checks["B has its own"] = as_session("B", perm.approved, "mail|outlook")
    checks["A's approval does not count for B"] = not as_session("B", perm.approved, "terminal|cmd")

    # positive control: the old behaviour (rewrite with only this session) loses A's approval
    data = json.load(open(perm._approved_file(), encoding="utf-8"))
    old = {"B": data["B"]}
    checks["control: the old rewrite would have dropped A"] = "A" not in old and "A" in data

    # expired approvals are still dropped
    data["C"] = {"x|y": time.time() - perm.APPROVED_TTL - 5}
    json.dump(data, open(perm._approved_file(), "w", encoding="utf-8"))
    as_session("A", perm.approve, "other|z")
    data = json.load(open(perm._approved_file(), encoding="utf-8"))
    checks["expired sessions are cleaned"] = "C" not in data and "A" in data and "B" in data

    # concurrency: 4 processes x 40 writes each, while this process keeps reading
    procs = [mp.Process(target=writer, args=(f"S{i}", 40, HOME)) for i in range(4)]
    for p in procs:
        p.start()
    bad_reads = 0
    while any(p.is_alive() for p in procs):
        for f in (perm._approved_file(), perm._cc_file(), perm._last_file()):
            try:
                json.load(open(f, encoding="utf-8"))
            except ValueError:
                bad_reads += 1
            except OSError:
                pass
    for p in procs:
        p.join()
    data = json.load(open(perm._approved_file(), encoding="utf-8"))
    checks["no reader ever saw half a file"] = bad_reads == 0
    checks["no approval lost by concurrent writers (4 x 40)"] = all(len(data.get(f"S{i}", {})) == 40 for i in range(4))
    cc = json.load(open(perm._cc_file(), encoding="utf-8"))
    checks["every session's permission mode is kept"] = all(f"S{i}" in cc for i in range(4))
    last = json.load(open(perm._last_file(), encoding="utf-8"))
    checks["every session's last question is kept"] = all(last.get(f"S{i}", {}).get("click", {}).get("key") == f"S{i}-39" for i in range(4))
    checks["no temporary files left behind"] = not [f for f in os.listdir(os.path.join(HOME, "perm")) if f.endswith(".tmp")]

    for k, v in checks.items():
        print(("ok   " if v else "FAIL ") + k)
    if not all(checks.values()):
        sys.exit(1)
    print(f"perm sessions verification passed ({len(checks)} checks)")
