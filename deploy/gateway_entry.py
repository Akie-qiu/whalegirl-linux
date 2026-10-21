"""Minimal container session with an immutable old WeChat client."""
import json
import os
from pathlib import Path
import pwd
import re
import signal
import subprocess
import time

VERSION = "4.1.0.16"
version = subprocess.check_output(["dpkg-query", "-W", "-f=${Version}", "wechat"], text=True).strip()
if version != VERSION:
    raise SystemExit(f"Refusing to start unexpected WeChat version: {version}")
subprocess.run(["sha256sum", "--check", "/opt/wechat-pinned.sha256"], check=True)
user = pwd.getpwnam("wechat")
data = Path("/data")
data.mkdir(exist_ok=True)
os.chown(data, user.pw_uid, user.pw_gid)
Path("/home/wechat").mkdir(exist_ok=True)
os.chown("/home/wechat", user.pw_uid, user.pw_gid)
Path("/tmp/.X11-unix").mkdir(mode=0o1777, exist_ok=True)
os.chmod("/tmp/.X11-unix", 0o1777)
Path("/run/dbus").mkdir(exist_ok=True)
if Path("/tmp/.X99-lock").exists():
    raise SystemExit("Existing X11 lock found; preserved for inspection, refusing duplicate display")
env = dict(os.environ, DISPLAY=":99", HOME="/home/wechat", WECHAT_HOME="/home/wechat",
           QT_ACCESSIBILITY="1", QT_LINUX_ACCESSIBILITY_ALWAYS_ON="1",
           QT_AUTO_SCREEN_SCALE_FACTOR="0", QT_ENABLE_HIGHDPI_SCALING="0", QT_SCALE_FACTOR="1",
           GTK_MODULES="gail:atk-bridge", AGENT_DB_PATH="/data/agent.db", AGENT_PORT="6174")
children = []

def spawn(argv, *, as_wechat=False):
    if as_wechat:
        argv = ["runuser", "-u", "wechat", "--", *argv]
    p = subprocess.Popen(argv, env=env, start_new_session=True)
    children.append(p)
    return p

def stop(signum, frame):
    for process in reversed(children):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
spawn(["Xvfb", ":99", "-screen", "0", "1280x800x24", "-nolisten", "tcp"], as_wechat=True)
time.sleep(1)
dbus = subprocess.check_output(["runuser", "-u", "wechat", "--", "dbus-launch", "--sh-syntax"], env=env, text=True)
match = re.search(r"DBUS_SESSION_BUS_ADDRESS='([^']+)'", dbus)
if not match:
    raise SystemExit("Unable to start private accessibility session")
env["DBUS_SESSION_BUS_ADDRESS"] = match.group(1)
spawn(["fluxbox"], as_wechat=True)
spawn(["dunst"], as_wechat=True)
bus_launcher = Path("/usr/libexec/at-spi-bus-launcher")
if bus_launcher.exists():
    spawn([str(bus_launcher)], as_wechat=True)
spawn(["pulseaudio", "--daemonize=no", "--exit-idle-time=-1"], as_wechat=True)
spawn(["/usr/bin/wechat"], as_wechat=True)
(data / "runtime-version.json").write_text(json.dumps({"wechat_version": version, "binary_hash_verified": True, "started_at": time.time()}))
os.chmod(data / "runtime-version.json", 0o644)
server = spawn(["/opt/agent-server/agent-server"])
raise SystemExit(server.wait())
