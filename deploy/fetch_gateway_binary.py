"""Verify and extract two small files from an immutable OCI image manifest."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import tarfile
import urllib.request

ROOT = Path("/opt/whalegirl-linux-assets")
DIGEST = "sha256:d6e31bea8e8ed7477fcfd92746826c8e24a31515734098af5f4eeec57b4791bd"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": "http://127.0.0.1:17890"}))
with opener.open("https://ghcr.io/token?service=ghcr.io&scope=repository:thisnick/agent-wechat:pull", timeout=30) as response:
    token = json.load(response)["token"]

def get(path):
    return opener.open(urllib.request.Request("https://ghcr.io/v2/thisnick/agent-wechat/" + path,
                                              headers={"Authorization": "Bearer " + token,
                                                       "Accept": "application/vnd.oci.image.manifest.v1+json"}), timeout=45)

with get("manifests/" + DIGEST) as response:
    raw = response.read()
if "sha256:" + hashlib.sha256(raw).hexdigest() != DIGEST:
    raise SystemExit("OCI manifest checksum mismatch")
manifest = json.loads(raw)
(ROOT / "gateway-manifest.json").write_text(json.dumps(manifest, indent=2))

def fetch(item):
    index, allowed = item
    layer = manifest["layers"][index]
    path = ROOT / f"gateway-layer-{index}.tar.gz"
    if not path.exists() or "sha256:" + hashlib.file_digest(path.open("rb"), "sha256").hexdigest() != layer["digest"]:
        with get("blobs/" + layer["digest"]) as response, path.open("wb") as output:
            while chunk := response.read(128 * 1024):
                output.write(chunk)
    if "sha256:" + hashlib.file_digest(path.open("rb"), "sha256").hexdigest() != layer["digest"]:
        raise ValueError("OCI layer checksum mismatch")
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            if member.name.lstrip("./") == allowed:
                if not member.isfile() or member.size > 64 * 1024**2:
                    raise ValueError("Unexpected binary archive entry")
                target = ROOT / "gateway-bin" / Path(allowed).name
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
                target.chmod(0o755)
                print("Verified " + allowed, flush=True)
                return
    raise ValueError("Expected binary missing from pinned layer")

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    list(pool.map(fetch, [(4, "usr/local/bin/sqlcipher"), (12, "opt/agent-server/agent-server")]))
