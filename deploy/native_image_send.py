"""Submit an approved ordinary image through the native Linux composer."""
import json
from pathlib import Path
import re
import subprocess
import time

ROOT = Path('/data/whalegirl-jobs')


def approved(chat, identity):
    if not re.fullmatch('[0-9a-f]{64}', str(identity)):
        raise ValueError('Invalid image identity')
    authority=json.loads((ROOT/'authorized-chats.json').read_text())
    assets=json.loads((ROOT/'image-assets.json').read_text())
    subset=authority.get('images_allowed_chat_ids') or authority.get('allowed_chat_ids', [])
    if (not authority.get('chat_enabled') or not authority.get('images_chat_enabled') or chat not in authority.get('allowed_chat_ids', []) or
            chat not in subset or assets.get('account_id')!=authority.get('account_id') or identity not in assets.get('assets', [])):
        raise ValueError('Image or conversation is no longer authorized')
    path=ROOT/'moments-assets'/(identity+'.png')
    if not path.is_file() or path.stat().st_size>24_000_000:
        raise ValueError('Approved image file is unavailable')
    return path


def send_image(job, path, root, name, is_group):
    # Import after chat_select_linux has finished initialization.
    from chat_select_linux import walk, tree, selected, run_process, click_node
    image=approved(job['chat'],job.get('asset_id'))
    editors=[n for n in walk(root) if n.get('role')=='text' and 'EDITABLE' in (n.get('states') or [])
             and (n.get('bounds') or {}).get('height',0)>35 and (n.get('bounds') or {}).get('y',0)>200]
    if len(editors)!=1:
        raise ValueError('Image composer was not uniquely identified')
    click_node(editors[0])
    run_process(['xdotool','key','--clearmodifiers','ctrl+a','BackSpace'],check=True,timeout=3)
    run_process(['xclip','-selection','clipboard','-t','image/png','-i',str(image)],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=3)
    run_process(['xdotool','key','--clearmodifiers','ctrl+v'],check=True,timeout=3)
    deadline=time.monotonic()+4
    button=None
    while time.monotonic()<deadline:
        current=tree()
        dialogs=[n for n in walk(current) if n.get('role')=='dialog' or
                 n.get('role')=='frame' and re.search(r'send.*(?:image|file)|发送.*(?:图片|文件)',n.get('name',''),re.I)]
        candidates=[n for parent in dialogs for n in walk(parent) if n.get('role')=='push-button' and
                    n.get('name','').strip().lower() in ('send','发送') and 'DISABLED' not in (n.get('states') or [])]
        if len(candidates)==1:
            button=candidates[0];break
        # Pinned Linux 4.1.1.8 embeds a pasted PNG in the rich composer rather
        # than opening a separate image dialog. This path was inspected live.
        inline=[n for n in walk(current) if n.get('role')=='push-button' and
                n.get('name','').strip() in ('Send(S)','Send','发送(S)','发送') and
                'DISABLED' not in (n.get('states') or [])]
        if not dialogs and len(inline)==1 and selected(current,name,is_group):
            button=inline[0];break
        time.sleep(.15)
    if not button:
        run_process(['xdotool','key','--clearmodifiers','Escape'],check=True,timeout=3)
        if selected(tree(),name,is_group):
            click_node(editors[0])
            run_process(['xdotool','key','--clearmodifiers','ctrl+a','BackSpace'],check=True,timeout=3)
        raise ValueError('Native image preview Send button not identified; image was not submitted')
    approved(job['chat'],job['asset_id'])
    if not selected(current,name,is_group):
        run_process(['xdotool','key','--clearmodifiers','Escape'],check=True,timeout=3)
        raise ValueError('Image destination changed before send')
    # Save the non-idempotent boundary before the one Send click.
    job['state']='sending';path.write_text(json.dumps(job,ensure_ascii=False))
    click_node(button)
    job['state']='submitted'
