"""Resolve user-authorized test targets and optionally perform one verified send each."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
import unicodedata
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument("--configure", action="store_true")
parser.add_argument("--send-probes", action="store_true")
parser.add_argument("--allow-single-contact", action="store_true", help="Test the uniquely authorized contact while the group has not appeared yet")
args = parser.parse_args()
DATA = Path("/var/lib/whalegirl-linux/core")
ASSETS = Path("/opt/whalegirl-linux-assets")
admin = (DATA / "admin.token").read_text().strip()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def api(path, data=None):
    request = urllib.request.Request("http://127.0.0.1:8092/api/" + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": "Bearer " + admin, "Content-Type": "application/json"})
    with opener.open(request, timeout=65) as response:
        return json.load(response)

def norm(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip()

targets = json.loads((ASSETS / "acceptance-targets.json").read_text())
if targets.get("authorized_by_user") is not True:
    raise SystemExit("Missing explicit user authorization for test destinations")
chats = api("gateway/chats")
matches = {}
for label, name, group in [("group", targets["group_name"], True), ("contact", targets["contact_name_or_remark"], False)]:
    found = {c["id"]: c for c in chats if c.get("isGroup") is group and any(norm(c.get(k)) == norm(name) for k in ("name", "remark"))}
    matches[label] = list(found.values())
summary = {key: [{"id": c["id"], "name": c["name"], "isGroup": c["isGroup"]} for c in value] for key, value in matches.items()}
print(json.dumps({"exact_matches": summary}, ensure_ascii=False), flush=True)
if any(len(rows) != 1 for rows in matches.values()):
    if args.allow_single_contact and len(matches["contact"]) == 1 and not matches["group"]:
        matches = {"contact": matches["contact"]}
        print("Authorized contact only; test group is not yet present", flush=True)
    else:
        raise SystemExit("Both targets must match uniquely; no messages sent and no configuration changed")

async def probes():
    from whalegirl_linux.gateway import AgentGateway
    gateway = AgentGateway("http://127.0.0.1:6175", (DATA / "gateway.token").read_text().strip())
    report = []
    previous = ASSETS / "outbound-acceptance.json"
    if previous.exists():
        raise SystemExit("Probe report already exists; inspect before any repeat send")
    # Persist intent before entering a non-idempotent send; restart never repeats probes.
    previous.write_text(json.dumps({"state": "started", "at": time.time()}, indent=2))
    try:
        for rows in matches.values():
            chat = rows[0]
            result = await gateway.send(chat["id"], "鲸鱼娘 Linux 旧版连接测试：这是一条部署验收消息。")
            report.append({"chat": chat["name"], "confirmed": result.confirmed, "message_id": result.message_id, "error": result.error})
            previous.write_text(json.dumps({"state": "completed" if len(report) == len(matches) else "partial", "results": report}, ensure_ascii=False, indent=2))
            if not result.confirmed:
                raise SystemExit("Probe send uncertain; no automatic retry")
            await asyncio.sleep(10)
    finally:
        await gateway.close()
    print(json.dumps({"outbound_probes": report}, ensure_ascii=False), flush=True)

if args.send_probes:
    asyncio.run(probes())
if args.configure:
    settings = api("config")
    settings.pop("api_key_configured", None)
    settings.update(allowed_chat_ids=[rows[0]["id"] for rows in matches.values()], enabled=True)
    value = api("config", {"settings": settings, "api_key": ""})
    print(json.dumps({"auto_reply_enabled": value["enabled"], "authorized_chat_count": len(value["allowed_chat_ids"])}, ensure_ascii=False), flush=True)
