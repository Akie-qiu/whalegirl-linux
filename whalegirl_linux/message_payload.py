"""Read bounded WeChat app-message XML without exposing attachment keys to prompts.

Also installed beside native_message_reader.py, which imports this pure module.
"""
from html import unescape
import re
import xml.etree.ElementTree as ET

MAX_ITEMS=80
MAX_TEXT=24000
MAX_DEPTH=3


def clip(value, limit=4000):
    text=str(value or '').replace('\x00','').strip()
    return text if len(text)<=limit else text[:limit]+'\n[内容较长，已截取]'


def xml_root(value):
    text=str(value or '').strip()
    if len(text)>1024*1024 or re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b',text,re.I):return None
    for _ in range(3):
        if text.startswith('<'):
            try:
                root=ET.fromstring(re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',text))
                for node in root.iter():node.tag=node.tag.rsplit('}',1)[-1]
                return root
            except ET.ParseError:return None
        decoded=unescape(text)
        if decoded==text:break
        text=decoded
    return None


def field(node, path, default=''):
    value=node.findtext(path)
    return (value or default).strip()


def embedded(node):
    if node is None:return ''
    return ET.tostring(node[0],encoding='unicode') if len(node) else (node.text or '')


def media_text(kind, content='', title=''):
    labels={3:'图片',34:'语音',43:'视频',47:'原生表情',42:'名片',48:'位置'}
    label=labels.get(kind,'媒体消息')
    description=''
    root=xml_root(content) if content.lstrip().startswith(('<msg','<emoji')) else None
    if kind==47 and root is not None:
        emoji=root if root.tag=='emoji' else root.find('.//emoji')
        if emoji is not None:description=emoji.get('desc','')
    return '['+label+('：'+clip(description or title,200) if description or title else '')+'；未附媒体内容]'


def app_node(body):
    root=xml_root(body)
    if root is None:return None
    return root if root.tag=='appmsg' else root.find('./appmsg')


def record_info(node, depth, budget):
    if depth>MAX_DEPTH:return {'items':[],'complete':False,'reason':'嵌套层级较深，未继续展开'}
    root=node if node is not None and node.tag=='recordinfo' else xml_root(embedded(node))
    if root is None:return {'items':[],'complete':False,'reason':'消息仅含预览，完整转发正文尚未取得'}
    if root.tag!='recordinfo':root=root.find('.//recordinfo')
    if root is None:return {'items':[],'complete':False,'reason':'未找到转发明细'}
    datalist=root.find('datalist')
    nodes=list(datalist.findall('dataitem')) if datalist is not None else []
    count=datalist.get('count','') if datalist is not None else ''
    total=int(count) if count.isdigit() else len(nodes)
    result={'title':clip(field(root,'title'),300),'summary':clip(field(root,'desc'),1500),
            'count':total,'items':[],'complete':True}
    for item in nodes:
        if budget['items']<=0 or budget['chars']<=0:
            result.update(complete=False,reason='转发较长，已截取可用明细');break
        budget['items']-=1
        kind=item.get('datatype') or field(item,'datatype')
        title=field(item,'datatitle') or field(item,'title')
        desc=field(item,'datadesc')
        entry={'sender':clip(field(item,'sourcename') or field(item,'datasrcname') or field(item,'dataitemsource/realchatname') or '转发记录中的用户',100),
               'time':clip(field(item,'sourcetime') or field(item,'srcMsgCreateTime'),60),
               'type':kind,'content':''}
        nested=item.find('recordinfo')
        if nested is None:nested=item.find('recorditem')
        if nested is None:
            nested=item.find('dataitemsource/recordinfo')
        if nested is None and desc.lstrip().startswith(('<recordinfo','&lt;recordinfo')):
            nested=xml_root(desc)
        if nested is not None:
            entry['forwarded']=record_info(nested,depth+1,budget)
            entry['content']='[嵌套合并转发] '+title
        elif kind=='1':
            entry['content']=desc or title
        elif kind=='2':entry['content']='[图片；未附图像]'+(' '+clip(title,200) if title else '')
        elif kind=='3':entry['content']='[语音；未转写]'
        elif kind=='37':
            entry['content']='[原生表情包；未附图像]'
            md5=field(item,'emojiitem/md5')
            if re.fullmatch('[0-9a-fA-F]{32}',md5):entry['sticker_md5']=md5.lower()
        elif kind in ('4','15'):entry['content']='[视频；未解析画面]'+(' '+clip(title,200) if title else '')
        elif kind=='8':entry['content']='[文件] '+(title or field(item,'datafmt') or '附件')
        elif desc.lstrip().startswith(('<msg','<appmsg')):
            parsed=parse_app_message(desc,depth=depth+1,budget=budget)
            entry['content']=render_app(parsed) if parsed else '[转发内嵌消息暂不可解析]'
        elif desc.lstrip().startswith('<') and xml_root(desc) is not None:
            entry['content']='[转发卡片，类型'+kind+'] '+title
        else:
            entry['content']='\n'.join(v for v in (title,desc) if v) or '[转发媒体或卡片，类型'+kind+']'
        entry['content']=clip(entry['content'],min(3000,max(0,budget['chars'])))
        budget['chars']-=len(entry['content'])+len(entry['sender'])+len(entry['time'])
        result['items'].append(entry)
    if len(result['items'])<total:
        result.update(complete=False,reason=result.get('reason') or '部分明细尚未取得或已截取')
    if not result['items']:
        result.update(complete=False,reason=result.get('reason') or '完整转发正文尚未取得')
    return result


def parse_app_message(body, *, depth=0, budget=None):
    node=app_node(body)
    if node is None:return {}
    budget=budget if budget is not None else {'items':MAX_ITEMS,'chars':MAX_TEXT}
    kind=field(node,'type')
    title=field(node,'title')
    value={'appType':kind,'content':clip(title,4000)}
    if kind in ('3','4','5'):
        value['content']=clip('\n'.join(v for v in ('[链接] '+title,field(node,'des'),field(node,'url')) if v),4000)
    reference=node.find('refermsg')
    if reference is not None:
        ref_kind=field(reference,'type')
        content=field(reference,'content')
        ref={'sender':clip(field(reference,'displayname'),100),'senderId':clip(field(reference,'fromusr'),200),
             'chatSenderId':clip(field(reference,'chatusr'),200),'serverId':field(reference,'svrid'),
             'type':int(ref_kind) if ref_kind.isdigit() else 1}
        if field(reference,'createtime'):ref['time']=clip(field(reference,'createtime'),60)
        if depth>=MAX_DEPTH:ref['content']='[更深层引用未展开]'
        elif ref['type']==49:
            parsed=parse_app_message(content,depth=depth+1,budget=budget)
            ref['content']=clip(render_app(parsed),8000) if parsed else '[引用的卡片内容暂不可解析]'
        elif ref['type']==1:ref['content']=clip(content,6000)
        else:ref['content']=media_text(ref['type'],content)
        value['reply']=ref
    if kind=='19':
        record=node.find('recorditem')
        if record is None:record=node.find('recordinfo')
        value['forwarded']=record_info(record,depth+1,budget)
        if not value['forwarded'].get('summary'):value['forwarded']['summary']=clip(field(node,'des'),1500)
    return value


def render_record(record,depth=0):
    if depth>MAX_DEPTH:return '[嵌套记录未展开]'
    result=['[合并转发聊天记录｜'+clip(record.get('title') or '聊天记录',300)+'｜'+str(record.get('count',len(record.get('items',[]))))+'条]']
    for i,item in enumerate(record.get('items',[])[:MAX_ITEMS],1):
        result.append(f"  {i}. [{clip(item.get('time'),60)}｜{clip(item.get('sender') or '记录用户',100)}] {clip(item.get('content'),3000)}")
        if item.get('forwarded'):result.append(render_record(item['forwarded'],depth+1))
    if not record.get('items') and record.get('summary'):result.append('[仅预览] '+clip(record['summary'],1500))
    if record.get('complete') is False:result.append('['+clip(record.get('reason') or '部分明细未取得',200)+'；不要声称已读完全部内容]')
    result.append('[合并转发结束]')
    return clip('\n'.join(result),MAX_TEXT)


def render_app(value):
    main=clip(value.get('content'),4000)
    if value.get('forwarded'):main=render_record(value['forwarded'])
    ref=value.get('reply')
    if isinstance(ref,dict):
        main='[当前发言] '+main+'\n[引用原话｜'+clip(ref.get('sender') or ref.get('senderId') or '原发言人',100)+(('｜'+clip(ref.get('time'),60)) if ref.get('time') else '')+']\n'+clip(ref.get('content'),8000)+'\n[引用结束]'
    return clip(main,MAX_TEXT)


def incoming_content(raw):
    payload={k:raw[k] for k in ('appType','reply','forwarded') if raw.get(k)}
    main=clip(raw.get('content'),4000)
    if not payload:return main,{}
    return render_app({**payload,'content':main}),payload
