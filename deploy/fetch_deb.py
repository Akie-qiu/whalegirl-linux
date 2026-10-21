"""Fetch the pinned archival package in verified HTTP ranges; retain all artifacts."""
import concurrent.futures
import hashlib
import json
import pathlib
import time
import urllib.request

ROOT = pathlib.Path("/opt/whalegirl-linux-assets")
URL = "https://github.com/trouter-ai/wechat-linux-versions/releases/download/v4.1.0.16/WeChatLinux_4.1.0.16_x86_64.deb"
SIZE = 210495128
SHA = "8445e4eb0074cd5dd1fa2dc34f70a548f1e1feeb7aeb452ad69dcbd2804d01a2"
ROOT.mkdir(parents=True, exist_ok=True)
DEST = ROOT / "wechat-4.1.0.16-verified.deb"
if DEST.exists() and hashlib.file_digest(DEST.open("rb"), "sha256").hexdigest() == SHA:
    print("Pinned WeChat package already verified", flush=True)
    raise SystemExit(0)

def fetch(index):
    begin, end = SIZE * index // 8, SIZE * (index + 1) // 8 - 1
    path = ROOT / f"wechat-old.range-{index}.part"
    for attempt in range(5):
        done = path.stat().st_size if path.exists() else 0
        if done == end - begin + 1:
            return path
        if done > end - begin + 1:
            raise ValueError("Existing chunk exceeds expected range; preserved for inspection")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": "http://127.0.0.1:17890"}))
        request = urllib.request.Request(URL, headers={"Range": f"bytes={begin + done}-{end}"})
        try:
            with opener.open(request, timeout=45) as response, path.open("ab") as output:
                expected = f"bytes {begin + done}-{end}/{SIZE}"
                if response.status != 206 or response.headers.get("Content-Range") != expected:
                    raise ValueError("Archive server did not honor exact HTTP range")
                while chunk := response.read(128 * 1024):
                    output.write(chunk)
            if path.stat().st_size == end - begin + 1:
                print(f"Package chunk {index + 1}/8 complete", flush=True)
                return path
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2)
    raise RuntimeError("Incomplete package chunk")

with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    paths = list(pool.map(fetch, range(8)))
with DEST.open("wb") as output:
    for path in paths:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                output.write(chunk)
digest = hashlib.file_digest(DEST.open("rb"), "sha256").hexdigest()
if digest != SHA:
    raise SystemExit("Checksum mismatch; package retained and will not be installed")
(ROOT / "package-verified.json").write_text(json.dumps({"version": "4.1.0.16", "sha256": SHA, "bytes": SIZE, "file": str(DEST), "reference": "flathub/com.tencent.WeChat commit 560da61"}, indent=2))
print("WeChat 4.1.0.16 SHA-256 verified", flush=True)
