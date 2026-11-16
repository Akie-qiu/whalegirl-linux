"""Cheap reference selection, never a reply policy or an emotion controller.

Only explicit surface cues and source context rank optional examples. Ambiguous
messages can select nothing; the main model still sees the complete dialogue.
"""
import json
import math
import re

from .topic_relevance import topic_terms


_CUES = {
    'distress': r'难过|失落|委屈|好累|有点累|累死|撑不住|心烦|烦死|不开心|安慰|共情|体贴|抱抱',
    'playful': r'哈哈|笑死|好笑|离谱|抽象|反转|吐槽|接梗|调侃|荒诞|绷不住|蚌埠住|乐了',
    'greeting': r'早上好|早安|中午好|晚上好|晚安|睡了|问好|寒暄|道晚安',
    'gratitude': r'谢谢|感谢|多谢|谢啦|道谢',
    'celebration': r'太好了|考过了|成功了|终于过了|开心|高兴|庆祝|恭喜',
    'question': r'怎么|为什么|为啥|怎么办|求助|请教|疑惑|询问|[?？]',
    'correction': r'不对|错了|错误的|不是这个意思|误解|纠正|澄清',
    'technical': r'报错|日志|接口|服务器|代码|数据库|驱动|蓝屏|[Bb][Uu][Gg]',
    'serious': r'认真|严肃|别开玩笑|不开玩笑|正经|急事',
}
_PATTERNS={key:re.compile(value) for key,value in _CUES.items()}


def signals(text):
    return {key for key,pattern in _PATTERNS.items() if pattern.search(str(text or ''))}


def reference_score(query, context, *, counterexamples='', current=None):
    """Return relevance for reference data only; zero means no useful match."""
    actual=signals(query if current is None else current)
    if not actual:actual=signals(query)
    offered=signals(context)
    negative=signals(counterexamples)
    if actual & negative:return 0
    if actual & {'distress','serious','technical'} and 'playful' in offered and not actual & {'playful'}:
        return 0
    common=(actual & offered)-{'question'}
    # Topic-word overlap alone is too weak for style material: old scene jokes
    # otherwise leak into unrelated chats that happen to share a common noun.
    if not common:return 0
    words=topic_terms(query)
    hits=len(words & topic_terms(context))
    return 4*len(common)+min(hits,6)


def reference_pool(items, *, limit=24):
    """Stable, varied candidates rebuilt off the reply path, not a rotating feed."""
    buckets={}
    for item in sorted(items,key=lambda row:(row.get('label') not in ('good','funny'),row['key'])):
        if not 2<=len(item.get('text',''))<=65:continue
        tags=signals(item.get('context') or item['text']) or {'other'}
        for tag in sorted(tags):buckets.setdefault(tag,[]).append(item)
    selected=[];keys=set();speakers={}
    for index in range(limit):
        for tag,rows in sorted(buckets.items()):
            if len(selected)>=limit:break
            for row in rows[index:index+1]:
                identity=row['key'];person=(tag,row['speaker'])
                if identity in keys or speakers.get(person,0)>=3:continue
                value={key:row[key] for key in ('key','speaker','text','context') if key in row}
                selected.append(value);keys.add(identity);speakers[person]=speakers.get(person,0)+1
    return selected


def select_examples(pool, query, *, current=None, limit=2, fallback_shape=False):
    if not query:return list(pool)[:limit]  # Admin preview; chat paths pass a real query.
    ranked=[(reference_score(query,item.get('context') or item['text'],current=current),item) for item in pool]
    ranked.sort(key=lambda pair:(-pair[0],pair[1]['key']))
    chosen=[]
    for score,item in ranked:
        if score<=0:continue
        if any(item['text']==old['text'] for old in chosen):continue
        chosen.append(item)
        if len(chosen)>=limit:break
    if fallback_shape and not chosen:
        actual=signals(query if current is None else current) or signals(query)
        want_question='question' in actual
        shape_ranked=[]
        for _,item in ranked:
            text=str(item.get('text') or '').strip()
            if not (2<=len(text)<=28):continue
            item_sig=signals(item.get('context') or text)
            if actual & {'distress','serious','technical'} and 'playful' in item_sig and not (actual & {'playful'}):
                continue
            if actual & {'distress','serious','technical','greeting','correction'} and not (actual & item_sig):
                continue
            ctx_q='?' in str(item.get('context') or '') or '？' in str(item.get('context') or '')
            shape_bonus=(2 if want_question==ctx_q else 0)+(2 if len(text)<=16 else 1)+(1 if item.get('context') else 0)
            shape_ranked.append((shape_bonus,item))
        shape_ranked.sort(key=lambda pair:(-pair[0],pair[1]['key']))
        for _,item in shape_ranked:
            if any(item['text']==old['text'] for old in chosen):continue
            chosen.append(item)
            if len(chosen)>=limit:break
    return chosen


def select_shape_examples(pool, query, *, current=None, limit=2):
    return select_examples(pool, query, current=current, limit=limit, fallback_shape=True)


def subtitle_excerpt(rows, allowed):
    """Sample contiguous pieces, retaining the actual source order of each line."""
    blocks=[];block=[];previous=None
    for index,row in enumerate(rows):
        if not isinstance(row,dict) or not isinstance(row.get('text'),str) or not row['text'].strip():continue
        try:
            start,end=float(row['start']),float(row['end'])
            if not all(math.isfinite(n) for n in (start,end)) or start<0 or end<start:continue
        except (KeyError,TypeError,ValueError,OverflowError):continue
        if block and (len(block)>=8 or index!=previous['line_index']+1 or start<previous['start'] or start-previous['end']>12):
            blocks.append(block);block=[]
        item={'line_index':index,'start':start,'end':end,'text':row['text'][:300]}
        block.append(item);previous=item
    if block:blocks.append(block)
    if not blocks:return []
    size=lambda value:len(json.dumps(value,ensure_ascii=False,separators=(',',':')))
    average=sum(size(value) for value in blocks)/len(blocks)
    count=max(2 if len(blocks)>=2 and allowed>=512 else 1,min(len(blocks),int(allowed/max(1,average))))
    indices=sorted({round(i*(len(blocks)-1)/max(1,count-1)) for i in range(count)})
    result=[]
    for index in indices:
        piece=[]
        for row in blocks[index]:
            if size(result+piece+[row])>allowed:break
            piece.append(row)
        # A single isolated line does not provide a context/reaction sequence.
        if len(piece)>=2 or len(blocks[index])==1:result.extend(piece)
    return result


def _line_times(row):
    try:
        start,end=float(row['start']),float(row['end'])
        if all(math.isfinite(n) for n in (start,end)) and 0<=start<=end:return start,end
    except (KeyError,TypeError,ValueError,OverflowError):pass
    return None


def reaction_evidence(event, source, submitted):
    """Verify a short reaction together with actual context, not model prose.

    Ordinary video comments are global unless their actual parent is offered.
    Subtitle utterances retain exact neighbouring source lines and time order;
    this proves supplied words, not speaker identity or that the speaker is human.
    Only timestamped danmaku can align with a nearby subtitle/visible scene.
    No credentials, identities or quotations are projected into chat references.
    """
    context=str(event.get('context_evidence') or '').strip()[:500]
    reaction=str(event.get('reaction_evidence') or '').strip()[:100]
    if len(context)<(2 if event.get('reaction_kind')=='subtitle' else 6) or len(reaction)<2 or reaction==context:return None
    result={'context':context,'reaction':reaction,'scope':'thread','source_id':source['id']}
    if source.get('kind')!='bilibili':return result if context in submitted and reaction in submitted else None
    try:material=json.loads(submitted)
    except (TypeError,ValueError):return None
    if not isinstance(material,dict):return None
    actual=[str(row.get(field) or '') for key,field in
            (('subtitles','text'),('scenes','visible'),('comments','text'),('danmaku','text'))
            for row in material.get(key,[]) if isinstance(row,dict)]
    if not any(context in value for value in actual):return None
    kind=event.get('reaction_kind');identity=str(event.get('reaction_id') or '')
    if kind=='subtitle':
        rows={row.get('line_index'):row for row in material.get('subtitles',[])
              if isinstance(row,dict) and isinstance(row.get('line_index'),int) and not isinstance(row.get('line_index'),bool)}
        left,right=event.get('context_line_index'),event.get('reaction_line_index')
        if left is None and right is None:
            for idx,row_a in sorted(rows.items()):
                row_b=rows.get(idx+1)
                if row_b and context in str(row_a.get('text') or '') and reaction in str(row_b.get('text') or ''):
                    left,right=idx,idx+1
                    break
        if any(not isinstance(n,int) or isinstance(n,bool) or n<0 for n in (left,right)) or right!=left+1:return None
        before,after=rows.get(left),rows.get(right)
        if not before or not after or context not in str(before.get('text') or '') or reaction not in str(after.get('text') or ''):return None
        first,second=_line_times(before),_line_times(after)
        if not first or not second or second[0]<first[0] or second[0]-first[1]>12:return None
        return {**result,'scope':'subtitle_sequence','reaction_kind':kind,'context_line_index':left,
                'reaction_line_index':right,'time':second[0],'range':[first[0],second[1]],
                'speaker_verification':'unverified','basis':'actual_subtitle_text'}
    if kind=='comment':
        rows=[row for row in material.get('comments',[]) if isinstance(row,dict)]
        observed=next((row for row in rows if str(row.get('id'))==identity and reaction in str(row.get('text') or '')),None)
        if not observed:return None
        parent=next((row for row in rows if str(row.get('id'))==str(observed.get('parent_id'))),None)
        # Only an observed reply to an observed parent proves an interaction.
        # A standalone comment remains discussion context, not a dialogue pair.
        if not parent or context not in str(parent.get('text') or ''):return None
        result.update(scope='reply_thread',reaction_kind=kind,reaction_id=identity,
                      parent_id=str(parent.get('id') or ''),root_id=str(observed.get('root_id') or parent.get('root_id') or parent.get('id') or ''))
        return result
    if kind!='danmaku':return None
    try:stamp=float(event.get('reaction_time'))
    except (TypeError,ValueError):return None
    observed=any(isinstance(row,dict) and row.get('time')==stamp and reaction in str(row.get('text') or '')
                 for row in material.get('danmaku',[]))
    if not observed:return None
    nearby=any(isinstance(row,dict) and context in str(row.get('text') or '') and
               float(row.get('start') or 0)-8<=stamp<=float(row.get('end') or 0)+8
               for row in material.get('subtitles',[]))
    nearby=nearby or any(isinstance(row,dict) and context in str(row.get('visible') or '') and
                        abs(float(row.get('time') or 0)-stamp)<=8 for row in material.get('scenes',[]))
    if not nearby:return None
    return {**result,'scope':'timed_window','reaction_kind':kind,'time':stamp}
