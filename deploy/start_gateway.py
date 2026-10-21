"""Start only the newly built old-WeChat container with bounded resources."""
import json
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT = Path("/var/lib/whalegirl-linux")
ASSETS = Path("/opt/whalegirl-linux-assets")
NAME = "whalegirl-wechat-old-01"
IMAGE = "whalegirl-wechat:4.1.0.16-20261020"
for leaf in ("gateway", "home"):
    (ROOT / leaf).mkdir(parents=True, exist_ok=True)
if subprocess.run(["docker", "container", "inspect", NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
    raise SystemExit("Container name already exists; preserved. Inspect before any replacement.")
args = ["docker", "run", "--detach", "--name", NAME,
        "--label", "com.whalegirl.role=wechat-old", "--restart", "on-failure:3", "--no-healthcheck",
        "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m,mode=1777", "--tmpfs", "/run:rw,nosuid,nodev,size=32m",
        "--security-opt", "seccomp=unconfined", "--cap-add", "SYS_PTRACE",
        "--memory", "512m", "--memory-swap", "1g", "--cpus", "0.65", "--pids-limit", "512", "--shm-size", "128m",
        "--publish", "127.0.0.1:6175:6174",
        "--mount", f"type=bind,src={ROOT / 'gateway'},dst=/data",
        "--mount", f"type=bind,src={ROOT / 'home'},dst=/home/wechat",
        "--mount", f"type=bind,src={ROOT / 'core/gateway.token'},dst=/data/auth-token,readonly",
        "--env", "AGENT_WECHAT_TOKEN=", IMAGE]
result = subprocess.run(args, capture_output=True, text=True, check=True)
print("Started dedicated old-WeChat container: " + result.stdout.strip()[:12], flush=True)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for attempt in range(30):
    try:
        with opener.open("http://127.0.0.1:6175/health", timeout=2) as response:
            value = json.load(response)
        break
    except Exception:
        time.sleep(1)
else:
    print(subprocess.run(["docker", "logs", "--tail", "50", NAME], capture_output=True, text=True).stderr[-3500:])
    raise SystemExit("Gateway health check failed; container and data retained")
version = json.loads((ROOT / "gateway/runtime-version.json").read_text())
report = {"container": NAME, "health": value, "verified_version": version,
          "resources": subprocess.check_output(["docker", "stats", "--no-stream", "--format", "{{json .}}", NAME], text=True).strip(),
          "qqbot_active": subprocess.run(["systemctl", "is-active", "qqbot-friend"], capture_output=True, text=True).stdout.strip()}
(ASSETS / "gateway-deploy.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report), flush=True)
