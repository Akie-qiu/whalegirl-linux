"""Read a rendered native image when this pinned build lacks an image key profile."""
import base64
from difflib import SequenceMatcher
from io import BytesIO
import re
from PIL import Image, ImageChops


def walk(node):
    yield node
    for child in node.get('children') or []:
        yield from walk(child)


def normal(value):
    return re.sub(r'\s+',' ',str(value or '')).strip()


def token(value):
    value=normal(re.split(r'\n(?:Quote |引用)',str(value or ''),maxsplit=1)[0])
    if re.fullmatch(r'Image|Photo|图片',value,re.I):return '[image]'
    if re.fullmatch(r'Animated Stickers|Stickers?|表情包|动画表情',value,re.I):return '[sticker]'
    return value


def correct_chat(root,name,is_group):
    nodes=list(walk(root));lists=[n for n in nodes if n.get('role')=='list' and n.get('name')=='Chats']
    if len(lists)!=1:return False
    selected=[n for n in lists[0].get('children') or [] if 'SELECTED' in (n.get('states') or [])]
    row=any(normal(n.get('name'))==name or normal(n.get('name')).startswith(name+' ') for n in selected)
    right=(lists[0].get('bounds') or {}).get('x',0)+(lists[0].get('bounds') or {}).get('width',0)
    header=any(n.get('role')=='label' and (normal(n.get('name'))==name or
               (is_group and re.fullmatch(re.escape(name)+r'\s*\(\s*\d+\s*\)',normal(n.get('name')))))
               and (n.get('bounds') or {}).get('x',-1)>right and (n.get('bounds') or {}).get('y',1000)<100 for n in nodes)
    return row and header


def locate_image(raw,nodes,local_id,kind,server_id=''):
    db_tokens=['[image]' if r.get('type')==3 else '[sticker]' if r.get('type')==47 else token(r.get('content')) for r in raw]
    ui_tokens=[token(n.get('name')) for n in nodes]
    targets=[i for i,r in enumerate(raw) if r.get('localId')==local_id and r.get('type')==kind and
             (not server_id or str(r.get('serverId'))==str(server_id))]
    if len(targets)!=1:return None
    target=targets[0]
    for block in SequenceMatcher(None,db_tokens,ui_tokens,autojunk=False).get_matching_blocks():
        if not (block.a<=target<block.a+block.size and block.size>=2):continue
        anchors=db_tokens[block.a:block.a+block.size]
        # A unique sequence also anchors an image next to a sticker or a quoted bubble.
        occurrences=sum(db_tokens[i:i+len(anchors)]==anchors for i in range(len(db_tokens)-len(anchors)+1))
        if occurrences==1:return nodes[block.b+target-block.a]
    return None


async def capture_message_image(gateway,chat,local_id,kind, *, message_time=None,server_id=''):
    chats=await gateway.chats();targets=[c for c in chats if c.get('id')==chat]
    if len(targets)!=1:return {}
    name=normal(targets[0].get('remark') or targets[0].get('name'))
    if gateway.ui_jobs_directory:
        await gateway.ui_job('open_chat',chat,scroll='latest')
    else:
        response=await gateway.client.post(f'/api/chats/{chat}/open',json={})
        response.raise_for_status()
        if response.json().get('ok') is not True:return {}
    before=await gateway.client.get('/api/debug/a11y');before.raise_for_status();root=before.json().get('tree') or {}
    if not correct_chat(root,name,targets[0].get('isGroup')):return {}
    lists=[n for n in walk(root) if n.get('role')=='list' and n.get('name')=='Messages']
    if len(lists)!=1:return {}
    nodes=[n for n in lists[0].get('children') or [] if n.get('role')=='list-item'
           and not re.fullmatch(r'\d{1,2}:\d{2}',normal(n.get('name')))]
    raw=await gateway.messages(chat,limit=200)
    if message_time and not any(str(r.get('serverId'))==str(server_id) for r in raw):
        raw=await gateway.messages(chat,limit=200,before_time=message_time+1,before_id=2147483647)
    raw=sorted(raw,key=lambda r:(str(r.get('timestamp') or ''),r.get('localId',0)))
    match=locate_image(raw,nodes,local_id,kind,server_id)
    for _ in range(2 if gateway.ui_jobs_directory and match is None else 0):
        gateway.media_history_moved=True
        await gateway.ui_job('open_chat',chat,scroll='up')
        before=await gateway.client.get('/api/debug/a11y');before.raise_for_status();root=before.json().get('tree') or {}
        if not correct_chat(root,name,targets[0].get('isGroup')):return {}
        lists=[n for n in walk(root) if n.get('role')=='list' and n.get('name')=='Messages']
        if len(lists)!=1:return {}
        nodes=[n for n in lists[0].get('children') or [] if n.get('role')=='list-item' and not re.fullmatch(r'\d{1,2}:\d{2}',normal(n.get('name')))]
        match=locate_image(raw,nodes,local_id,kind,server_id)
        if match is not None:break
    if match is None:return {}
    response=await gateway.client.get('/api/debug/screenshot');response.raise_for_status()
    encoded=response.json().get('base64') or ''
    if encoded.startswith('data:'):encoded=encoded.split(',',1)[1]
    after=await gateway.client.get('/api/debug/a11y');after.raise_for_status();after_root=after.json().get('tree') or {}
    if not correct_chat(after_root,name,targets[0].get('isGroup')):return {}
    if not any(n.get('role')=='list-item' and n.get('name')==match.get('name') and n.get('bounds')==match.get('bounds')
               for n in walk(after_root)):return {}
    image=Image.open(BytesIO(base64.b64decode(encoded))).convert('RGB')
    b=match.get('bounds') or {};area=lists[0].get('bounds') or {}
    left=max(0,b.get('x',0),area.get('x',0));top=max(0,b.get('y',0),area.get('y',0))
    right=min(image.width,b.get('x',0)+b.get('width',0),area.get('x',0)+area.get('width',0))
    bottom=min(image.height,b.get('y',0)+b.get('height',0),area.get('y',0)+area.get('height',0))
    if right-left<80 or bottom-top<80:return {}
    cropped=image.crop((int(left),int(top),int(right),int(bottom)))
    click_xy=None
    # Remove the blank chat-row background and avatar so the model sees the image itself.
    background=cropped.getpixel((cropped.width-1,cropped.height-1))
    mask=ImageChops.difference(cropped,Image.new('RGB',cropped.size,background)).convert('L').point(lambda p:255 if p>10 else 0)
    projection=list(mask.resize((cropped.width,1),Image.Resampling.BOX).getdata())
    threshold=255*max(35/cropped.height,.15)
    runs=[];start=None
    for x,value in enumerate(projection+[0]):
        if value>threshold and start is None:start=x
        if value<=threshold and start is not None:runs.append((start,x));start=None
    if runs:
        run_left,run_right=max(runs,key=lambda r:r[1]-r[0])
        bounds=mask.crop((run_left,0,run_right,cropped.height)).getbbox()
        if run_right-run_left>=40 and bounds and bounds[3]-bounds[1]>=60:
            click_xy=[left+(run_left+run_right)/2,top+(bounds[1]+bounds[3])/2]
            cropped=cropped.crop((max(0,run_left-2),max(0,bounds[1]-2),min(cropped.width,run_right+2),min(cropped.height,bounds[3]+2)))
    if click_xy and gateway.ui_jobs_directory and kind==3:
        result=await gateway.ui_job('copy_image',chat,node_name=match.get('name'),bounds=match.get('bounds'),xy=click_xy,local_id=local_id)
        if result.get('data'):
            return {'type':'image','format':'png','data':result['data'],'quality':'native_clipboard','partial':False}
    scale=min(4,1024/max(cropped.size))
    if scale>1:cropped=cropped.resize((round(cropped.width*scale),round(cropped.height*scale)),Image.Resampling.LANCZOS)
    output=BytesIO();cropped.save(output,format='JPEG',quality=88)
    return {'type':'image','format':'jpeg','data':base64.b64encode(output.getvalue()).decode(),
            'quality':'visible_chat_thumbnail','partial':top>b.get('y',0) or bottom<b.get('y',0)+b.get('height',0)}
