"""Root-only, narrowly scoped refresh of the native WeChat login QR widget."""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import time
import urllib.request

DATA = Path("/var/lib/whalegirl-linux/core")
RESPONSE = DATA / "qr-refresh.response"

def walk(node):
    yield node
    for child in node.get("children") or []:
        yield from walk(child)


def signout_popup_ok(tree):
    """Return the OK button of a benign sign-out notice, never a security warning."""
    candidates = []
    for node in walk(tree or {}):
        if node.get("role") not in ("frame", "dialog", "alert", "filler", "layered-pane", "panel", "window"):
            continue
        children = list(walk(node))
        ok_buttons = [c for c in children if c.get("role") == "push-button"
                      and re.fullmatch(r"OK|Confirm|确定|确认", c.get("name", ""), re.I)]
        if len(ok_buttons) != 1:
            continue
        labels = [c.get("name", "").strip() for c in children
                  if c.get("role") in ("label", "static") and c.get("name", "").strip()
                  and not re.fullmatch(r"Tip|OK|Confirm|确定|确认", c.get("name", "").strip(), re.I)]
        if labels:
            candidates.append((len(children), "\n".join(dict.fromkeys(labels)).strip(), ok_buttons[0]))
    if not candidates:
        return None
    _, text, button = min(candidates, key=lambda item: item[0])
    if re.fullmatch(r"You have signed out of Weixin\.?|已退出微信\.?|你已退出微信\.?", text, re.I):
        return button
    return None


def refresh():
    container = os.environ.get("WHALEGIRL_QR_CONTAINER", "whalegirl-wechat-old-01")
    if container not in {"whalegirl-wechat-old-01", "whalegirl-wechat-candidate-4118"}:
        raise RuntimeError("未批准的微信二维码容器，未点击")
    token = (DATA / "gateway.token").read_text().strip()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def api(route, post=False):
        request = urllib.request.Request("http://127.0.0.1:6175/api/" + route, data=b"{}" if post else None,
                                         headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with opener.open(request, timeout=12) as response:
            return json.load(response)

    tree = api("debug/a11y").get("tree") or {}
    nodes = list(walk(tree))
    ok_buttons = [n for n in nodes if n.get("role") == "push-button" and re.fullmatch(r"OK|Confirm|确定|确认", n.get("name", ""), re.I)]
    signout_ok = signout_popup_ok(tree) if len(ok_buttons) == 1 else None
    if signout_ok is not None:
        bounds = signout_ok.get("bounds") or {}
        x, y, w, h = [bounds.get(k, -1) for k in ("x", "y", "width", "height")]
        if not (350 <= x <= 800 and 250 <= y <= 650 and 40 <= w <= 220 and 20 <= h <= 70):
            raise RuntimeError("退出提示框按钮位置异常，未点击")
        subprocess.run(["docker", "exec", "--env", "DISPLAY=:99", "--user", "wechat", container,
                        "/opt/tools/click", str(round(x + w / 2)), str(round(y + h / 2))],
                       capture_output=True, check=True, timeout=8)
        for _ in range(6):
            time.sleep(0.3)
            tree = api("debug/a11y").get("tree") or {}
            nodes = list(walk(tree))
            if not any(n.get("role") == "push-button" and re.fullmatch(r"OK|Confirm|确定|确认", n.get("name", ""), re.I) for n in nodes):
                break
    names = {n.get("name", "") for n in nodes}
    if any(n.get("role") == "push-button" and re.fullmatch(r"OK|Confirm|确定|确认", n.get("name", ""), re.I) for n in nodes):
        raise RuntimeError("客户端有提示框，已保留，不能自动确认或刷新")
    if any(n.get('role') == 'label' and re.search(r'confirm.*phone|comfirm.*phone|手机确认',n.get('name',''),re.I) for n in nodes):
        return {'ok':True,'refreshed':False,'mode':'phone_confirmation','message':'请在手机微信确认这次 Linux 登录'}
    login_buttons=[n for n in nodes if n.get('role')=='push-button' and n.get('name') in ('Log In', 'Enter Weixin')]
    current_user=any(n.get('role')=='label' and n.get('name','').startswith('Current User') for n in nodes)
    if len(login_buttons)==1 and current_user:
        bounds=login_buttons[0].get('bounds') or {}
        x,y,w,h=[bounds.get(k,-1) for k in ('x','y','width','height')]
        if not (400<=x<=750 and 350<=y<=550 and 100<=w<=250 and 25<=h<=70):
            raise RuntimeError('微信登录按钮位置异常，未点击')
        subprocess.run(['docker','exec','--env','DISPLAY=:99','--user','wechat',container,
                        '/opt/tools/click',str(round(x+w/2)),str(round(y+h/2))],
                       capture_output=True,check=True,timeout=8)
        for _ in range(12):
            time.sleep(.35)
            after=list(walk(api('debug/a11y').get('tree') or {}))
            if any(n.get('role')=='label' and re.search(r'confirm.*phone|comfirm.*phone|手机确认',n.get('name',''),re.I) for n in after):
                return {'ok':True,'refreshed':True,'mode':'phone_confirmation','message':'请在手机微信确认这次 Linux 登录'}
            if {n.get('name','') for n in after}.intersection({'Scan to log in', '扫码登录', '扫描二维码登录'}):
                current = api('status/login', True).get('qrDataUrl', '')
                if current:
                    return {'ok':True,'refreshed':True,'qrDataUrl':current,'message':'已切换到二维码页面，请扫码并在手机确认'}
        raise RuntimeError('已点击微信登录，但未读到手机确认提示；请检查客户端')
    if not names.intersection({"Scan to log in", "扫码登录", "扫描二维码登录"}):
        if api("status/auth").get("status") == "logged_in":
            return {"ok": True, "refreshed": False, "message": "微信已登录"}
        raise RuntimeError("当前不是已识别的登录二维码页面，未点击")
    buttons = [n for n in nodes if n.get("role") == "push-button" and n.get("name") in ("QR Code", "二维码")]
    if len(buttons) != 1:
        raise RuntimeError("二维码控件未唯一匹配，未点击")
    bounds = buttons[0].get("bounds") or {}
    x, y, w, h = [bounds.get(k, -1) for k in ("x", "y", "width", "height")]
    if not (0 <= x < 1280 and 0 <= y < 800 and 50 <= w <= 400 and 50 <= h <= 400):
        raise RuntimeError("二维码控件位置异常，未点击")
    previous = api("status/login", True).get("qrDataUrl", "")
    subprocess.run(["docker", "exec", "--env", "DISPLAY=:99", "--user", "wechat",
                    container, "/opt/tools/click", str(round(x + w / 2)), str(round(y + h / 2))],
                   capture_output=True, check=True, timeout=8)
    current = ""
    for _ in range(8):
        time.sleep(0.5)
        current = api("status/login", True).get("qrDataUrl", "")
        if current and current != previous:
            return {"ok": True, "refreshed": True, "qrDataUrl": current,
                    "message": "客户端二维码已刷新"}
    # Clicking an unexpired QR widget can legitimately leave the same code.
    # status/login decodes the current native screenshot, so reuse that observed
    # code without falsely claiming that it changed or refreshing repeatedly.
    if current:
        return {"ok": True, "refreshed": False, "qrDataUrl": current,
                "message": "已读取客户端当前二维码，请扫码并在手机确认"}
    raise RuntimeError("未读取到客户端二维码，请检查微信提示；不会继续重复点击")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--operator", action="store_true")
    args = parser.parse_args()
    nonce = "operator"
    if not args.operator:
        request = json.loads((DATA / "qr-refresh.request").read_text())
        nonce = request.get("nonce", "")
        if not re.fullmatch(r"[0-9a-f]{32}", nonce):
            raise SystemExit("Invalid refresh request")
    try:
        if RESPONSE.exists():
            last = json.loads(RESPONSE.read_text())
            if time.time() - last.get("at", 0) < 10:
                raise RuntimeError("请等待十秒后再刷新二维码")
        result = refresh()
    except Exception as exc:
        result = {"ok": False, "message": str(exc)[:300] if isinstance(exc, RuntimeError) else type(exc).__name__}
    result.update(nonce=nonce, at=time.time())
    temporary = RESPONSE.with_suffix(".pending")
    temporary.write_text(json.dumps(result, ensure_ascii=False))
    user = pwd.getpwnam("whalegirl-ai")
    os.chown(temporary, user.pw_uid, user.pw_gid)
    temporary.chmod(0o600)
    os.replace(temporary, RESPONSE)
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
