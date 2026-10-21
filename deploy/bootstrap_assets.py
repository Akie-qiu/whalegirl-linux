"""Download pinned assets without executing the WeChat installer or removing files."""
import hashlib
import json
import pathlib
import subprocess

ROOT = pathlib.Path("/opt/whalegirl-linux-assets")
DEB_URL = "https://github.com/trouter-ai/wechat-linux-versions/releases/download/v4.1.0.16/WeChatLinux_4.1.0.16_x86_64.deb"
DEB_SHA = "8445e4eb0074cd5dd1fa2dc34f70a548f1e1feeb7aeb452ad69dcbd2804d01a2"
BASE = "ghcr.io/thisnick/agent-wechat@sha256:d6e31bea8e8ed7477fcfd92746826c8e24a31515734098af5f4eeec57b4791bd"
ROOT.mkdir(parents=True, exist_ok=True)
deb = ROOT / "wechat-4.1.0.16-amd64.deb"
print("Downloading pinned WeChat 4.1.0.16...", flush=True)
with (ROOT / "download.log").open("a") as log:
    subprocess.run(["curl", "--fail", "--location", "--retry", "2", "--connect-timeout", "15", "--max-time", "600", "--continue-at", "-", "--output", str(deb), DEB_URL], stdout=log, stderr=log, check=True)
digest = hashlib.file_digest(deb.open("rb"), "sha256").hexdigest()
if digest != DEB_SHA:
    raise SystemExit("Downloaded package checksum mismatch; preserved for inspection and not installed")
metadata = subprocess.check_output(["dpkg-deb", "--field", str(deb), "Package", "Version", "Architecture"], text=True)
if "4.1.0.16" not in metadata or "amd64" not in metadata:
    raise SystemExit("Unexpected Debian package metadata")
print("WeChat archive hash and version verified: " + metadata.replace("\n", "; "), flush=True)
(ROOT / "assets.json").write_text(json.dumps({"wechat_version": "4.1.0.16", "deb_sha256": DEB_SHA, "deb_source": DEB_URL, "checksum_reference": "flathub/com.tencent.WeChat commit 560da61", "base_image": BASE}, indent=2))
print("Pulling immutable gateway base image...", flush=True)
with (ROOT / "image-pull.log").open("a") as log:
    subprocess.run(["docker", "pull", BASE], stdout=log, stderr=log, check=True, timeout=900)
print("Assets ready; no WeChat container has been started.", flush=True)
