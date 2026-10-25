"""Prepare one isolated Linux candidate; preserve the previous container and data."""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
SPEC = json.loads((ROOT / "candidate-4.1.1.8.json").read_text())
PACKAGE = ROOT / "wechat-4.1.1.8-verified.deb"
DATA = Path("/var/lib/whalegirl-linux/candidate-4.1.1.8")


def verify():
    if PACKAGE.stat().st_size != SPEC["bytes"]:
        raise RuntimeError("Package length mismatch; retained without installation")
    with PACKAGE.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != SPEC["sha256"]:
        raise RuntimeError("Package SHA-256 mismatch; retained without installation")
    fields = subprocess.check_output(["dpkg-deb", "--field", str(PACKAGE), "Package", "Version", "Architecture"], text=True)
    metadata = dict(line.split(": ", 1) for line in fields.splitlines())
    if metadata != {"Package": "wechat", "Version": SPEC["version"], "Architecture": SPEC["architecture"]}:
        raise RuntimeError("Unexpected package metadata")
    report = {**SPEC, "verified_at": time.time(), "package_metadata": metadata}
    (ROOT / "package-verified.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"verified": True, "version": SPEC["version"], "sha256": digest}), flush=True)


def download():
    if PACKAGE.exists():
        verify()
        return

    def chunk(index):
        begin, end = SPEC["bytes"] * index // 6, SPEC["bytes"] * (index + 1) // 6 - 1
        path = ROOT / f"package.range-{index}.part"
        for attempt in range(4):
            done = path.stat().st_size if path.exists() else 0
            if done == end - begin + 1:
                return path
            if done > end - begin + 1:
                raise RuntimeError("Oversized prior range; preserved for inspection")
            request = urllib.request.Request(SPEC.get("mirror_url") or SPEC["download_url"], headers={"Range": f"bytes={begin + done}-{end}", "User-Agent": "WhaleGirlDeployment/0.1"})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(request, timeout=25) as response:
                    if response.status != 206 or response.headers.get("Content-Range") != f"bytes {begin + done}-{end}/{SPEC['bytes']}":
                        raise RuntimeError("Download server did not honor exact range")
                    with path.open("ab") as output:
                        while block := response.read1(262144):
                            output.write(block)
                if path.stat().st_size == end - begin + 1:
                    print(f"Candidate download range {index + 1}/6 complete", flush=True)
                    return path
            except Exception as exc:
                if attempt == 3:
                    raise
                print(f"Range {index + 1} retry {attempt + 1}: {type(exc).__name__}", flush=True)
                time.sleep(2)
        raise RuntimeError("Incomplete download")

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        parts = list(pool.map(chunk, range(6)))
    with PACKAGE.open("xb") as output:
        for path in parts:
            with path.open("rb") as stream:
                while block := stream.read(1048576):
                    output.write(block)
    verify()


def build():
    verify()
    base_id = subprocess.check_output(["docker", "image", "inspect", SPEC["base_image"], "--format", "{{.Id}}"], text=True).strip()
    entry = (ROOT / "gateway_entry.py").read_text()
    if entry.count('VERSION = "4.1.0.16"') != 1:
        raise RuntimeError("Unexpected base entrypoint; no build attempted")
    (ROOT / "gateway_entry-candidate.py").write_text(entry.replace('VERSION = "4.1.0.16"', 'VERSION = "4.1.1.8"'))
    dockerfile = f'''FROM {SPEC["base_image"]}
USER root
COPY {PACKAGE.name} /opt/{PACKAGE.name}
RUN echo '{SPEC["sha256"]}  /opt/{PACKAGE.name}' | sha256sum --check \\
    && dpkg -i /opt/{PACKAGE.name} \\
    && test "$(dpkg-query -W -f='${{Version}}' wechat)" = '{SPEC["version"]}' \\
    && apt-mark hold wechat \\
    && sha256sum /opt/wechat/wechat > /opt/wechat-pinned.sha256
COPY gateway_entry-candidate.py /opt/gateway_entry.py
'''
    (ROOT / "Dockerfile").write_text(dockerfile)
    (ROOT / ".dockerignore").write_text(f"*\n!Dockerfile\n!{PACKAGE.name}\n!gateway_entry-candidate.py\n")
    with (ROOT / "build.log").open("a") as log:
        subprocess.run(["docker", "build", "--pull=false", "--network=none", "--tag", SPEC["image"], str(ROOT)], stdout=log, stderr=log, check=True)
    (ROOT / "build-verified.json").write_text(json.dumps({"base_image_id": base_id, "image": SPEC["image"], "built_at": time.time()}, indent=2))
    print("Candidate image built with pinned package and no network during build", flush=True)


def start():
    if subprocess.run(["docker", "inspect", SPEC["container"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        raise RuntimeError("Candidate container already exists; preserved without replacement")
    subprocess.run(["docker", "image", "inspect", SPEC["image"]], stdout=subprocess.DEVNULL, check=True)
    for leaf in ("gateway", "home"):
        (DATA / leaf).mkdir(parents=True, exist_ok=True)
    # The previous client cannot log in. Stop it to keep within the host RAM budget.
    subprocess.run(["docker", "stop", "--time", "15", "whalegirl-wechat-old-01"], check=True)
    argv = ["docker", "run", "--detach", "--name", SPEC["container"], "--label", "com.whalegirl.role=wechat-candidate",
        "--init", "--restart", "unless-stopped", "--no-healthcheck", "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m,mode=1777", "--tmpfs", "/run:rw,nosuid,nodev,size=32m",
        "--security-opt", "seccomp=unconfined", "--cap-add", "SYS_PTRACE",
        "--memory", "1280m", "--memory-swap", "2560m", "--cpus", "1", "--pids-limit", "1024", "--shm-size", "128m",
        "--publish", f"127.0.0.1:{SPEC['host_port']}:6174",
        "--mount", f"type=bind,src={DATA / 'gateway'},dst=/data",
        "--mount", f"type=bind,src={DATA / 'home'},dst=/home/wechat",
        "--mount", "type=bind,src=/var/lib/whalegirl-linux/core/gateway.token,dst=/data/auth-token,readonly",
        "--env", "AGENT_WECHAT_TOKEN=", SPEC["image"]]
    try:
        subprocess.run(argv, check=True)
    except Exception:
        subprocess.run(["docker", "start", "whalegirl-wechat-old-01"], check=False)
        raise
    print(json.dumps({"candidate_started": SPEC["container"], "port": SPEC["host_port"], "previous_container": "stopped_and_preserved"}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["download", "build", "start"])
    globals()[parser.parse_args().action]()
