"""Pure sequence matching shared with the native helper; no image dependencies."""
from difflib import SequenceMatcher
import re


def token(value):
    value=re.sub(r'\s+',' ',re.split(r'\n(?:Quote |引用)',str(value or ''),maxsplit=1)[0]).strip()
    if re.fullmatch(r'Image|Photo|图片',value,re.I):return '[image]'
    if re.fullmatch(r'Animated Stickers|Stickers?|表情包|动画表情',value,re.I):return '[sticker]'
    return value


def locate_image(raw,nodes,local_id,kind,server_id=''):
    db_tokens=['[image]' if r.get('type')==3 else '[sticker]' if r.get('type')==47 else token(r.get('content')) for r in raw]
    ui_tokens=[token(n.get('name')) for n in nodes]
    targets=[i for i,r in enumerate(raw) if r.get('localId')==local_id and r.get('type')==kind and
             (not server_id or str(r.get('serverId'))==str(server_id))]
    if len(targets)!=1:return None
    target=targets[0]
    if len(raw)==len(nodes)==1 and db_tokens==ui_tokens:return nodes[0]
    for block in SequenceMatcher(None,db_tokens,ui_tokens,autojunk=False).get_matching_blocks():
        if not (block.a<=target<block.a+block.size and block.size>=2):continue
        anchors=db_tokens[block.a:block.a+block.size]
        occurrences=sum(db_tokens[i:i+len(anchors)]==anchors for i in range(len(db_tokens)-len(anchors)+1))
        if occurrences==1:return nodes[block.b+target-block.a]
    return None
