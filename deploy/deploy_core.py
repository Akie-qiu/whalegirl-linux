"""Install this independent service; no existing bot service is stopped or edited."""
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

ASSETS = Path("/opt/whalegirl-linux-assets")
BASE = Path("/opt/whalegirl-linux")
DATA = Path("/var/lib/whalegirl-linux/core")
BASE.mkdir(exist_ok=True)
release = BASE / "releases" / time.strftime("%Y%m%d-%H%M%S")
release.mkdir(parents=True)
with tarfile.open(ASSETS / "core.tar") as archive:
    for member in archive:
        name = Path(member.name)
        if name.is_absolute() or ".." in name.parts or member.issym() or member.islnk():
            raise SystemExit("Unsafe deployment archive member")
    archive.extractall(release, filter="data")

try:
    user = pwd.getpwnam("whalegirl-ai")
except KeyError:
    subprocess.run(["useradd", "--system", "--user-group", "--home-dir", str(DATA), "--shell", "/usr/sbin/nologin", "whalegirl-ai"], check=True)
    user = pwd.getpwnam("whalegirl-ai")
DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
venv = BASE / "venv"
print("Preparing isolated Python environment...", flush=True)
if not (venv / "bin/python").exists():
    result = subprocess.run(["python3", "-m", "venv", str(venv)], capture_output=True, text=True)
    if result.returncode:
        subprocess.run(["apt-get", "update"], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["apt-get", "install", "-y", "python3-venv"], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["python3", "-m", "venv", str(venv)], check=True)
python = str(venv / "bin/python")
with (ASSETS / "core-install.log").open("a") as log:
    env = dict(os.environ, PIP_INDEX_URL="https://mirrors.aliyun.com/pypi/simple/", HTTPS_PROXY="", HTTP_PROXY="", https_proxy="", http_proxy="", NO_PROXY="127.0.0.1,localhost")
    subprocess.run([python, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(release / "requirements.txt")], env=env, stdout=log, stderr=log, check=True)
    lock = subprocess.check_output([python, "-m", "pip", "freeze"], text=True)
    (release / "requirements.lock").write_text(lock)
print("Running offline core acceptance tests on Ubuntu...", flush=True)
with (ASSETS / "core-tests.log").open("w") as log:
    subprocess.run([python, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=release, stdout=log, stderr=log, check=True)

# Initialize separate encrypted credentials. Never print or replace the existing QQ key.
sys.path.insert(0, str(release))
sys.path.insert(0, str(venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"))
from whalegirl_linux.settings import Config, Settings
config = Config(DATA)
existing_key = Path("/var/lib/qqbot-ai/runtime/deepseek-api.key")
if existing_key.exists() and not config.api_key():
    model = "deepseek-chat"
    existing_config = Path("/var/lib/qqbot-ai/runtime/config.json")
    if existing_config.exists():
        value = json.loads(existing_config.read_text()).get("deepseek_model")
        if isinstance(value, str) and 0 < len(value) <= 100:
            model = value
    config.save(Settings(model=model), existing_key.read_text().strip())
for path in [DATA, *DATA.rglob("*")]:
    os.chown(path, user.pw_uid, user.pw_gid)
    path.chmod(0o700 if path.is_dir() else 0o600)

current = BASE / "current"
pending = BASE / "current.pending"
if pending.exists() or pending.is_symlink():
    raise SystemExit("Pending release link already exists; preserved for inspection")
pending.symlink_to(release, target_is_directory=True)
os.replace(pending, current)
shutil.copy2(release / "deploy/whalegirl-linux.service", "/etc/systemd/system/whalegirl-linux.service")
shutil.copy2(release / "deploy/qr_control.py", BASE / "qr_control.py")
for unit in ("whalegirl-qr-refresh.path", "whalegirl-qr-refresh.service"):
    shutil.copy2(release / "deploy" / unit, Path("/etc/systemd/system") / unit)
subprocess.run(["systemctl", "daemon-reload"], check=True)
subprocess.run(["systemctl", "enable", "--now", "whalegirl-qr-refresh.path"], check=True)
subprocess.run(["systemctl", "enable", "whalegirl-linux"], check=True)
subprocess.run(["systemctl", "restart", "whalegirl-linux"], check=True)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for _ in range(20):
    try:
        with opener.open("http://127.0.0.1:8092/health", timeout=2) as response:
            health = json.load(response)
        break
    except Exception:
        time.sleep(0.5)
else:
    raise SystemExit("New core service did not become healthy")
report = {"release": str(release), "health": health, "offline_tests": "passed", "model": config.settings.model, "api_key_configured": bool(config.api_key()), "auto_reply_enabled": config.settings.enabled}
if config.api_key():
    request = urllib.request.Request("http://127.0.0.1:8092/api/model/test", data=b"{}", headers={"Authorization": "Bearer " + config.admin_token, "Content-Type": "application/json"})
    try:
        with opener.open(request, timeout=50) as response:
            report["model_probe"] = json.load(response)
    except Exception as exc:
        report["model_probe"] = {"ok": False, "error": type(exc).__name__}
(ASSETS / "core-deploy.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False), flush=True)
