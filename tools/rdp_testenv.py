"""A test Remote Desktop server for tests/test_rdp_live.py: xrdp in an ISOLATED WSL distro named 'pcc-rdp' (it does not touch other
distros or Windows' own settings). Remove it completely with:  python tools/rdp_testenv.py --remove

  python tools/rdp_testenv.py            create the distro (Ubuntu 24.04), install xrdp + a minimal desktop, user pcc, port 3390
  python tools/rdp_testenv.py --start    start xrdp and keep the distro alive while you test (Ctrl+C to stop)

The remote desktop shows a text editor (mousepad) and a dialog with a button 'Aceptar remoto' to click. Test password: pcc-test-2026.
"""
import argparse, os, subprocess, sys, tempfile

SETUP = r"""set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q xrdp xorgxrdp openbox xterm mousepad zenity dbus-x11 fonts-dejavu-core x11-xserver-utils
adduser xrdp ssl-cert >/dev/null 2>&1 || true
id pcc >/dev/null 2>&1 || useradd -m -s /bin/bash pcc
echo 'pcc:pcc-test-2026' | chpasswd
sed -i 's/^port=3389/port=3390/' /etc/xrdp/xrdp.ini
cat > /home/pcc/.xsession <<'X'
#!/bin/sh
xsetroot -solid "#2b3a4a"
mousepad &
zenity --info --title="PCC Remote" --text="Remote test window" --ok-label="Aceptar remoto" &
exec openbox-session
X
chmod +x /home/pcc/.xsession; chown pcc:pcc /home/pcc/.xsession
systemctl enable xrdp >/dev/null 2>&1 || true
echo "setup done"
"""


def wsl(*args, **kw):
    return subprocess.run(["wsl.exe", *args], env={**os.environ, "WSL_UTF8": "1"}, **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--start", action="store_true")
    a = ap.parse_args()
    if a.remove:
        wsl("--unregister", "pcc-rdp"); print("removed"); return 0
    if a.start:
        print("xrdp on localhost:3390 - Ctrl+C to stop")
        return wsl("-d", "pcc-rdp", "-u", "root", "--", "bash", "-c", "systemctl start xrdp; exec sleep infinity").returncode
    loc = os.path.join(os.path.expanduser("~"), ".pc-control-testenv", "wsl")
    names = wsl("-l", "-q", capture_output=True, text=True).stdout
    if "pcc-rdp" not in names:
        wsl("--install", "Ubuntu-24.04", "--name", "pcc-rdp", "--location", loc, "--no-launch", check=True)
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, newline="\n") as f:
        f.write(SETUP)
    path = "/mnt/" + f.name[0].lower() + f.name[2:].replace("\\", "/")
    r = wsl("-d", "pcc-rdp", "-u", "root", "--", "bash", path)
    os.remove(f.name)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
