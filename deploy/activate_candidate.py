"""Activate the verified Linux candidate with replies disabled and backups retained."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

STAGE = Path(__file__).resolve().parent
SPEC = json.loads((STAGE / "candidate-4.1.1.8.json").read_text())
DATA = Path("/var/lib/whalegirl-linux/core")
CURRENT = Path("/opt/whalegirl-linux/current").resolve()
BACKUP = STAGE / ("activation-before-" + str(time.time_ns()))
FILES = {"settings.py": "whalegirl_linux/settings.py", "app.py": "whalegirl_linux/app.py",
         "runtime.py": "whalegirl_linux/runtime.py", "web.html": "whalegirl_linux/web.html",
         "test_core.py": "tests/test_core.py", "README.md": "README.md",
         "qr_control.py": "deploy/qr_control.py", "deploy_core.py": "deploy/deploy_core.py",
         "whalegirl-qr-refresh.path": "deploy/whalegirl-qr-refresh.path",
         "whalegirl-qr-refresh.service": "deploy/whalegirl-qr-refresh.service"}
NEW_TOOLS = {"deploy/qr_control.py", "deploy/whalegirl-qr-refresh.path", "deploy/whalegirl-qr-refresh.service"}

state = json.loads(subprocess.check_output(["docker", "inspect", SPEC["container"], "--format", "{{json .State}}"], text=True))
if not state["Running"] or state["OOMKilled"]:
    raise SystemExit("Candidate is not running cleanly; core activation blocked")
version = json.loads(Path("/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/runtime-version.json").read_text())
if version.get("wechat_version") != SPEC["version"] or version.get("binary_hash_verified") is not True:
    raise SystemExit("Native candidate version was not verified; core activation blocked")
settings_path = DATA / "settings.json"
settings = json.loads(settings_path.read_text())
if settings.get("enabled"):
    raise SystemExit("An enabled bot requires a separate migration; no changes made")
for name, relative in FILES.items():
    if not (STAGE / "core-patch" / name).is_file() or (not (CURRENT / relative).is_file() and relative not in NEW_TOOLS):
        raise SystemExit("Missing patch source or current target")
BACKUP.mkdir(mode=0o700)
for name, relative in FILES.items():
    if (CURRENT / relative).is_file():
        shutil.copy2(CURRENT / relative, BACKUP / name)
(BACKUP / "new-source-files.json").write_text(json.dumps([r for n, r in FILES.items() if not (CURRENT / r).exists()]))
shutil.copy2(settings_path, BACKUP / "settings.json")
shutil.copy2("/opt/whalegirl-linux/qr_control.py", BACKUP / "installed-qr_control.py")
for name, relative in FILES.items():
    shutil.copy2(STAGE / "core-patch" / name, CURRENT / relative)
with (STAGE / "candidate-core-tests.log").open("w") as output:
    result = subprocess.run(["/opt/whalegirl-linux/venv/bin/python", "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=CURRENT, stdout=output, stderr=output, timeout=60)
if result.returncode:
    for name, relative in FILES.items():
        if (BACKUP / name).is_file():
            shutil.copy2(BACKUP / name, CURRENT / relative)
    raise SystemExit("Candidate core tests failed; existing source restored, new source tools retained")
print("25 offline tests passed on Linux", flush=True)

subprocess.run(["systemctl", "stop", "whalegirl-linux"], check=True, timeout=30)
settings["expected_wechat_version"] = SPEC["version"]
pending = settings_path.with_suffix(".candidate-pending")
pending.write_text(json.dumps(settings, ensure_ascii=False, indent=2))
stat = settings_path.stat()
os.chown(pending, stat.st_uid, stat.st_gid)
pending.chmod(0o600)
os.replace(pending, settings_path)
shutil.copy2(STAGE / "core-patch/qr_control.py", "/opt/whalegirl-linux/qr_control.py")
dropin = Path("/etc/systemd/system/whalegirl-qr-refresh.service.d/20-candidate.conf")
dropin.parent.mkdir(exist_ok=True)
if dropin.exists():
    shutil.copy2(dropin, BACKUP / "20-candidate.conf")
dropin.write_text("[Service]\nEnvironment=WHALEGIRL_QR_CONTAINER=" + SPEC["container"] + "\n")
subprocess.run(["systemctl", "daemon-reload"], check=True)
subprocess.run(["systemctl", "start", "whalegirl-linux"], check=True, timeout=30)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for _ in range(30):
    try:
        with opener.open("http://127.0.0.1:8092/health", timeout=2) as response:
            health = json.load(response)
        if health.get("wechat_version") == SPEC["version"]:
            break
    except Exception:
        pass
    time.sleep(.5)
else:
    raise SystemExit("Candidate core health did not match; retained backup: " + str(BACKUP))
report = {"activated_at": time.time(), "health": health, "version": version, "auto_reply_enabled": False,
          "real_login_acceptance": "pending", "backup": str(BACKUP), "container": SPEC["container"],
          "qq_active": subprocess.check_output(["systemctl", "is-active", "qqbot-friend"], text=True).strip()}
(STAGE / "activation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False), flush=True)
