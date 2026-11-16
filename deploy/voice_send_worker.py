#!/usr/bin/env python3
"""Prepare and send voice notes for the Rust voice job API.

Adapted from thisnick/agent-wechat's MIT-licensed voice-send-worker.
See VOICE_WORKER_LICENSE.txt. Keep native GUI serialization in the gateway.
"""

import base64
import json
import os
from pathlib import Path
import re
import signal
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import wave
import xml.etree.ElementTree as ET

RATE = 48_000
MAX_SAMPLES = 50 * RATE
MIN_SAMPLES = 2 * RATE
EDGE_SAMPLES = RATE // 4
API = "http://127.0.0.1:6174"


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def command(*args, timeout=30, input_data=None):
    env = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
    result = subprocess.run(args, input=input_data, capture_output=True, timeout=timeout, env=env)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr.decode(errors='replace')[-400:]}")
    return result.stdout


def pulse(*args):
    return command("runuser", "-u", "wechat", "--", "env", "HOME=/home/wechat", "pactl", *args).decode()


def read_pcm(path):
    with wave.open(str(path), "rb") as audio:
        if (audio.getnchannels(), audio.getframerate(), audio.getsampwidth()) != (1, RATE, 2):
            raise RuntimeError("Prepared audio must be mono 48 kHz s16 PCM")
        return audio.readframes(audio.getnframes())


def write_pcm(path, samples):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(RATE)
        audio.writeframes(samples)
    path.chmod(0o644)


def bounds(total):
    if total <= 0:
        raise RuntimeError("No decoded audio samples")
    parts = [(start, min(start + MAX_SAMPLES, total)) for start in range(0, total, MAX_SAMPLES)]
    if len(parts) > 1 and parts[-1][1] - parts[-1][0] < MIN_SAMPLES:
        deficit = MIN_SAMPLES - (parts[-1][1] - parts[-1][0])
        previous_start, previous_end = parts[-2]
        parts[-2] = (previous_start, previous_end - deficit)
        parts[-1] = (previous_end - deficit, total)
    return parts


def prepare(directory):
    status_path = directory / "status.json"
    status = json.loads(status_path.read_text())
    status["status"] = "preparing"
    save(status_path, status)
    source = directory / "source.wav"
    command("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(directory / "input"), "-vn", "-ac", "1", "-ar", str(RATE),
            "-c:a", "pcm_s16le", str(source), timeout=300)
    original = read_pcm(source)
    count = len(original) // 2
    if count > 10 * 60 * RATE:
        raise RuntimeError("Voice source exceeds the ten-minute decoded duration limit")
    silence = bytes(EDGE_SAMPLES * 2)
    chunks = []
    for index, (start, end) in enumerate(bounds(count)):
        section = original[start * 2:end * 2]
        tail_padding = max(0, MIN_SAMPLES - (end - start))
        path = directory / f"chunk-{index + 1:03d}.wav"
        write_pcm(path, silence + section + bytes(tail_padding * 2) + silence)
        chunks.append({"index": index, "status": "prepared", "sourceStartSample": start,
                       "sourceEndSample": end, "sourceDurationSeconds": (end - start) / RATE,
                       "playbackDurationSeconds": (end - start + tail_padding + 2 * EDGE_SAMPLES) / RATE,
                       "path": path.name, "messageId": None})
    reconstructed = b"".join(read_pcm(directory / part["path"])[EDGE_SAMPLES * 2:
                             (EDGE_SAMPLES + part["sourceEndSample"] - part["sourceStartSample"]) * 2]
                             for part in chunks)
    if reconstructed != original:
        raise RuntimeError("Prepared audio failed source reconstruction")
    check_cancel(directory)
    status["sourceDurationSeconds"] = count / RATE
    status["chunks"] = chunks
    status["status"] = "prepared"
    save(status_path, status)


def api(path):
    token = Path("/data/auth-token").read_text().strip()
    request = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)


def messages(chat_id):
    return api("/api/messages/" + urllib.parse.quote(chat_id, safe="") + "?limit=50")


def walk(node):
    yield node
    for child in node.get("children", []):
        yield from walk(child)


def tree():
    return json.loads(command("a11y-dump", "--format", "json").decode())


def named(root, role, name):
    return [node for node in walk(root) if node.get("role") == role
            and node.get("name") == name and node.get("bounds")]


def destination_ok(root, display_name):
    selected = [node for node in walk(root) if node.get("role") == "list-item"
                and node.get("name", "").startswith(display_name)
                and "SELECTED" in node.get("states", [])]
    header = [node for node in walk(root) if node.get('role') == 'label' and
              re.fullmatch(re.escape(display_name) + r'(?:\s*[（(]\s*\d+\s*[)）])?', node.get('name', ''))]
    return bool(selected and header)


def control(root, name):
    matches = named(root, "push-button", name)
    # Some Qt accessibility trees expose one physical control twice.
    matches = list({tuple(sorted(node['bounds'].items())): node for node in matches}.values())
    if len(matches) != 1:
        raise RuntimeError(f"{name} control is missing or ambiguous")
    return matches[0]


def click(node):
    b = node["bounds"]
    x = int(b["x"] + b["width"] / 2)
    y = int(b["y"] + b["height"] / 2)
    command("xdotool", "mousemove", str(x), str(y), "click", "1")


def check_cancel(directory):
    if (directory / "cancel").exists():
        raise InterruptedError("Voice job cancelled")
    expected_parent = os.environ.get("AGENT_SERVER_PID")
    if expected_parent and os.getppid() != int(expected_parent):
        raise RuntimeError("Server restarted during voice job")


def capture_ready():
    source = None
    for line in pulse("list", "sources", "short").splitlines():
        fields = line.split("\t")
        if len(fields) >= 2 and fields[1] == "agent_voice_mic":
            source = int(fields[0])
    if source is None:
        raise RuntimeError("Virtual microphone is unavailable")
    streams = []
    for block in pulse("list", "source-outputs").split("Source Output #")[1:]:
        number = re.match(r"(\d+)", block)
        current = re.search(r"(?m)^\s*Source: (\d+)\s*$", block)
        if number and current and 'application.process.binary = "wechat"' in block:
            streams.append((number.group(1), int(current.group(1))))
    if len(streams) > 1:
        raise RuntimeError("WeChat capture stream is ambiguous")
    if not streams:
        return False
    stream, current = streams[0]
    if current != source:
        pulse("move-source-output", stream, "agent_voice_mic")
        return False
    return True


def cancel_recorder():
    click(control(tree(), "Cancel"))


def silk_duration(data):
    """Validate Tencent SILK packets; this does not decode or compare speech."""
    if not data.startswith(b'\x02#!SILK_V3'):
        raise ValueError('Not a Tencent SILK stream')
    position, packets = 10, 0
    while position < len(data):
        if position + 2 > len(data):
            raise ValueError('Truncated SILK packet length')
        size = struct.unpack_from('<h', data, position)[0]
        position += 2
        if size == -1 and position == len(data):
            break
        if size <= 0 or position + size > len(data):
            raise ValueError('Invalid or incomplete SILK packet')
        position += size
        packets += 1
    if not packets or position != len(data):
        raise ValueError('Empty or incomplete SILK stream')
    return packets * .02


def verified_payload(part, matched, media):
    if (matched.get('type') != 34 or not matched.get('isSelf') or
            not str(matched.get('serverId') or '').isdigit() or int(matched['serverId']) <= 0):
        raise RuntimeError('Voice does not have an outgoing server acknowledgement')
    data = base64.b64decode(media.get('data') or '', validate=True)
    if not data:
        raise RuntimeError('Sent voice bytes are not available')
    if data.startswith(b'\x02#!SILK_V3'):
        encoded = silk_duration(data)
        content = str(matched.get('content') or '')
        if len(content) > 100_000 or '<!DOCTYPE' in content or '<!ENTITY' in content:
            raise RuntimeError('Invalid voice metadata')
        root = ET.fromstring(content[content.index('<'):])
        voice = root if root.tag == 'voicemsg' else root.find('.//voicemsg')
        if voice is None or voice.get('voiceformat') != '4':
            raise RuntimeError('Native voice metadata does not identify SILK')
        duration = int(voice.get('voicelength', '0')) / 1000
        if abs(encoded-duration) > .12:
            raise RuntimeError('SILK packets and native voice duration disagree')
        part.update(encodedDurationSeconds=encoded,
                    verification='silk_packets_and_native_server_receipt', audioContentVerified=False)
        part.pop('decodedDurationSeconds', None)
    else:
        decoded = command('ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                          '-i', 'pipe:0', '-ac', '1', '-ar', str(RATE), '-f', 's16le', 'pipe:1',
                          input_data=data, timeout=30)
        duration = len(decoded) / (2 * RATE)
        part.update(decodedDurationSeconds=duration,
                    verification='decoded_duration_and_native_server_receipt', audioContentVerified=False)
    playback = part['playbackDurationSeconds']
    maximum = max(playback + 1, part.get('recordingElapsedSeconds', playback) + .5)
    if not 0 < duration <= 55 or duration < playback-.25 or duration > maximum:
        raise RuntimeError('Retrieved voice duration does not cover the recorded clip')
    return duration


def verify_voice(directory, status, part, prior_ids, display_name):
    chat_id = status["chatId"]
    deadline = time.monotonic() + 30
    matched = None
    while time.monotonic() < deadline:
        fresh = [item for item in messages(chat_id) if item["localId"] not in prior_ids
                 and item.get('isSelf') and item.get('type') == 34]
        if len(fresh) > 1:
            raise RuntimeError("More than one new message appeared after voice Send")
        if (len(fresh) == 1 and str(fresh[0].get('serverId') or '').isdigit()
                and int(fresh[0]['serverId']) > 0):
            matched = fresh[0]
            break
        time.sleep(0.5)
    if matched is None:
        raise RuntimeError("Voice Send outcome is unknown after 30 seconds")
    part["messageId"] = matched["localId"]
    media = api("/api/messages/" + urllib.parse.quote(chat_id, safe="") +
                "/media/" + str(matched["localId"]))
    # The native database and retrieved media are scoped to this exact chat.
    # Do not depend on an incidental bubble label or claim FFmpeg decoded SILK.
    verified_payload(part, matched, media)
    part["status"] = "verified"
    save(directory / "status.json", status)


def reconcile(directory):
    """Recheck saved message IDs only. Never records, clicks or resubmits."""
    status = json.loads((directory / 'status.json').read_text())
    auth = api('/api/status/auth')
    if auth.get('status') != 'logged_in' or auth.get('loggedInUser') != status.get('accountId'):
        raise RuntimeError('Account changed; the saved voice was not reconciled')
    rows = messages(status['chatId'])
    for part in status.get('chunks', []):
        identity = part.get('messageId')
        if identity is None:
            raise RuntimeError('No saved message ID; cannot reconcile without a new send')
        matches = [row for row in rows if str(row.get('localId')) == str(identity)]
        if len(matches) != 1 or matches[0].get('chatId') != status['chatId']:
            raise RuntimeError('Saved voice message is not unique or available')
        media = api('/api/messages/' + urllib.parse.quote(status['chatId'], safe='') + '/media/' + str(identity))
        verified_payload(part, matches[0], media)
        part['status'] = 'verified'
    if not status.get('chunks'):
        raise RuntimeError('No saved chunks to reconcile')
    status.update(status='completed', error=None)
    save(directory / 'status.json', status)


def send(directory):
    status_path = directory / "status.json"
    status = json.loads(status_path.read_text())
    chat_id = status["chatId"]
    chat = api("/api/chats/" + urllib.parse.quote(chat_id, safe=""))
    if not chat:
        raise RuntimeError("Destination chat was not found")
    display_name = "File Transfer" if chat_id == "filehelper" else chat["name"]
    first_pid = command("pgrep", "-o", "-f", "/usr/bin/wechat").strip()
    if not first_pid:
        raise RuntimeError("WeChat is not running")
    # The pinned legacy entrypoint did not create an input device. Always
    # prepare the devices before opening a recorder; this is idempotent.
    command('runuser', '-u', 'wechat', '--', 'env', 'HOME=/home/wechat',
            '/opt/tools/voice-audio-ready', timeout=25)
    status["status"] = "sending"
    save(status_path, status)
    for part in status["chunks"]:
        check_cancel(directory)
        if command("pgrep", "-o", "-f", "/usr/bin/wechat").strip() != first_pid:
            raise RuntimeError("WeChat restarted during voice job")
        root = tree()
        if not destination_ok(root, display_name):
            raise RuntimeError("Destination is not selected")
        if not named(root, 'push-button', 'Send Voice'):
            raise RuntimeError('Native voice recording is unavailable in this WeChat build; '
                               'WeChat 4.1.13 or later with an input device is required; nothing was sent')
        check_cancel(directory)
        before = {item["localId"] for item in messages(chat_id)}
        started = time.monotonic()
        click(control(root, "Send Voice"))
        try:
            while time.monotonic() - started < 5:
                current = tree()
                if destination_ok(current, display_name) and named(current, 'push-button', 'Cancel') and capture_ready():
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("Recorder UI and capture stream did not appear in five seconds")
            part["status"] = "recording"
            save(status_path, status)
            player = subprocess.Popen(["runuser", "-u", "wechat", "--", "env", "HOME=/home/wechat",
                                       "paplay", "--device=agent_voice_feed", str(directory / part["path"])],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                      start_new_session=True)
            try:
                while player.poll() is None:
                    check_cancel(directory)
                    if time.monotonic() - started >= 55:
                        raise TimeoutError("Recording reached the 55-second deadline")
                    time.sleep(0.1)
                if player.returncode:
                    raise RuntimeError("Audio playback failed")
            finally:
                if player.poll() is None:
                    try:
                        os.killpg(player.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        player.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(player.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        player.wait(timeout=2)
            if time.monotonic() - started >= 55:
                raise TimeoutError("Recording reached the 55-second deadline")
            check_cancel(directory)
            root = tree()
            if not destination_ok(root, display_name):
                raise RuntimeError("Destination changed while recording")
            if time.monotonic() - started >= 55:
                raise TimeoutError("Recording reached the 55-second deadline during control refresh")
            if command("pgrep", "-o", "-f", "/usr/bin/wechat").strip() != first_pid:
                raise RuntimeError("WeChat restarted during recording")
            check_cancel(directory)
            click(control(root, "Send voice message"))
            part["status"] = "send_clicked"
            part["recordingElapsedSeconds"] = time.monotonic() - started
            save(status_path, status)
        except Exception:
            if part["status"] != "send_clicked":
                try:
                    cancel_recorder()
                    part["status"] = "cancelled" if (directory / "cancel").exists() else "failed"
                    if {item["localId"] for item in messages(chat_id)} != before:
                        part["status"] = "unknown"
                except Exception:
                    part["status"] = "unknown"
                save(status_path, status)
            raise
        try:
            verify_voice(directory, status, part, before, display_name)
        except Exception:
            part["status"] = "unknown"
            save(status_path, status)
            raise
    status["status"] = "completed"
    save(status_path, status)


def main():
    mode = sys.argv[1]
    directory = Path(sys.argv[2]).resolve()
    if mode == "prepare":
        prepare(directory)
    elif mode == "send":
        send(directory)
    elif mode == 'reconcile':
        reconcile(directory)
    else:
        raise RuntimeError("Expected prepare, send or reconcile mode")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        directory = Path(sys.argv[2]).resolve()
        status_path = directory / "status.json"
        if status_path.exists():
            status = json.loads(status_path.read_text())
            status["status"] = "cancelled" if isinstance(exc, InterruptedError) else "needs_review" if any(
                part["status"] in ("send_clicked", "unknown", "verified") for part in status.get("chunks", [])
            ) else "failed"
            status["error"] = str(exc)
            save(status_path, status)
        print(f"voice job stopped: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        if len(sys.argv) > 1 and sys.argv[1] == 'send':
            try:
                sys.path.insert(0, '/data/native-tools')
                from native_voice_compat import modern_client, restore_main
                if modern_client():
                    import chat_select_linux as ui
                    ui.SKIP_MESSAGES = True
                    restore_main(ui)
            except Exception as exc:
                # Delivery verification was already persisted; restoring focus
                # must never downgrade a confirmed send or trigger a resend.
                print('voice UI restore: ' + type(exc).__name__, file=sys.stderr)
