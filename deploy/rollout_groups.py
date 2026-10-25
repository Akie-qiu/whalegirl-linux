"""Install group UI selection and shorter polling, retaining the prior container."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

STAGE = Path(__file__).resolve().parent
NAME = "whalegirl-wechat-candidate-4118"
IMAGE = "whalegirl-wechat:4.1.1.8-groups-20261020"
CURRENT = Path("/opt/whalegirl-linux/current").resolve()
DATA = Path("/var/lib/whalegirl-linux/core")
BACKUP = STAGE / ("groups-before-" + str(time.time_ns()))
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path, body=None):
    request = urllib.request.Request("http://127.0.0.1:8092" + path,
                                    data=None if body is None else json.dumps(body).encode(),
                                    headers={"Authorization": "Bearer " + (DATA / "admin.token").read_text().strip(),
                                             "Content-Type": "application/json"})
    with OPENER.open(request, timeout=10) as response:
        return json.load(response)


old = json.loads(subprocess.check_output(["docker", "inspect", NAME], text=True))[0]
if old["Config"]["Image"] != "whalegirl-wechat:4.1.1.8-ui-20261020":
    raise SystemExit("Unexpected source container image; inspect before rollout")
settings = api("/api/config")
settings.pop("api_key_configured", None)
state = api("/api/state")
if state.get("processing") or state.get("paused_reason"):
    raise SystemExit("Reply processing or review is active; wait before rollout")
BACKUP.mkdir(mode=0o700)
(BACKUP / "settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2))
os.chmod(BACKUP / "settings.json", 0o600)
for relative in ["whalegirl_linux/settings.py", "whalegirl_linux/runtime.py", "README.md"]:
    target = BACKUP / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CURRENT / relative, target)

with (STAGE / "group-image-build.log").open("w") as output:
    subprocess.run(["docker", "build", "--pull=false", "--network=none", "--file",
                    str(STAGE / "Dockerfile.gateway-groups"), "--tag", IMAGE, str(STAGE)],
                   stdout=output, stderr=output, check=True, timeout=60)
print(json.dumps({"phase": "image_ready", "image": IMAGE}), flush=True)

api("/api/config", {"settings": {**settings, "enabled": False}, "api_key": ""})
subprocess.run(["systemctl", "stop", "whalegirl-linux"], check=True, timeout=30)
for relative, source in [("whalegirl_linux/settings.py", "settings.py"),
                         ("whalegirl_linux/runtime.py", "runtime.py")]:
    shutil.copy2(STAGE / source, CURRENT / relative)

previous = NAME + "-before-groups-" + str(int(time.time()))
subprocess.run(["docker", "stop", "--time", "15", NAME], check=True, capture_output=True, timeout=25)
subprocess.run(["docker", "rename", NAME, previous], check=True)
argv = ["docker", "run", "--detach", "--name", NAME,
        "--init", "--restart", "unless-stopped", "--no-healthcheck", "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
        "--tmpfs", "/run:rw,nosuid,nodev,size=32m",
        "--security-opt", "seccomp=unconfined", "--cap-add", "SYS_PTRACE",
        "--memory", "1280m", "--memory-swap", "2560m", "--cpus", "1",
        "--pids-limit", "1024", "--shm-size", "128m",
        "--publish", "127.0.0.1:6175:6174"]
for mount in old["Mounts"]:
    source = mount["Name"] if mount["Type"] == "volume" else mount["Source"]
    flag = f"type={mount['Type']},src={source},dst={mount['Destination']}"
    if not mount["RW"]:
        flag += ",readonly"
    argv.extend(["--mount", flag])
argv.extend(["--env", "AGENT_WECHAT_TOKEN=", IMAGE])
try:
    subprocess.run(argv, check=True, capture_output=True, timeout=30)
except Exception:
    print(json.dumps({"phase": "start_failed", "retained_previous": previous,
                      "backup": str(BACKUP)}), flush=True)
    raise
subprocess.run(["systemctl", "start", "whalegirl-linux"], check=True, timeout=30)
report = {"phase": "awaiting_native_login", "container": NAME, "image": IMAGE,
          "previous": previous, "backup": str(BACKUP), "time": time.time()}
(STAGE / "group-rollout.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report), flush=True)
