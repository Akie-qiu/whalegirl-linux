"""Small, provenance-bound video evidence reads; no playback or platform writes."""
import asyncio
import bisect
import html
import io
import math
import re
import zlib
import xml.etree.ElementTree as ET

from PIL import Image

from .bilibili_client import API, BiliError
from .video_scene import select_frames,storyboards
from .video_media import media_frames


def text(value, cap=500):
    return html.unescape(re.sub('<[^>]*>', '', str(value or ''))).strip()[:cap]


def valid_bvid(value):
    return isinstance(value, str) and re.fullmatch(r'BV[A-Za-z0-9]{10}', value) is not None


def snapshot_url(value):
    value=str(value)
    return 'https:'+value if value.startswith('//') else 'https://'+value[7:] if value.startswith('http://') else value


def spaced(rows, count):
    if len(rows) <= count:
        return rows
    return [rows[round(i*(len(rows)-1)/(count-1))] for i in range(count)] if count > 1 else rows[:1]


def subtitle_segments(rows, duration):
    if not isinstance(rows, list) or len(rows) > 30000:
        raise BiliError('invalid_subtitles', '字幕结构或大小异常，未用于学习')
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            start, end = float(row.get('from', -1)), float(row.get('to', -1))
        except (TypeError, ValueError):
            raise BiliError('invalid_subtitles', '字幕时间点异常，未用于学习') from None
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end <= duration+5):
            raise BiliError('invalid_subtitles', '字幕时间与视频时长不符，未用于学习')
        content = text(row.get('content'), 2000)
        if content:
            result.append({'start': start, 'end': end, 'text': content})
    return sorted(result, key=lambda row: row['start'])


def subtitle_fraction(segments, duration):
    covered = end = 0
    for row in segments:
        covered += max(0, min(duration, row['end'])-max(end, row['start']))
        end = max(end, min(duration, row['end']))
    return round(covered/duration, 4) if duration else 0


def crop_frames(raw, positions, x_count, y_count, width, height):
    with Image.open(io.BytesIO(raw)) as image:
        if image.width*image.height > 16_000_000 or image.width < x_count*width or image.height < y_count*height:
            raise BiliError('frame_size', '视频快照尺寸不符，未用于理解画面')
        result = []
        for index, stamp in positions:
            left, top = index % x_count*width, index//x_count*height
            frame = image.crop((left, top, left+width, top+height)).convert('RGB')
            output = io.BytesIO();frame.save(output, format='JPEG', quality=82)
            result.append({'time': stamp, 'jpeg': output.getvalue()})
        return result


class VideoReader:
    def __init__(self,client):
        self.client=client;self.optional_exhausted=False

    async def optional(self,operation,label,notes):
        if self.optional_exhausted:
            operation.close();return None
        try:return await operation
        except BiliError as error:
            if error.code in ('risk_paused','access_stopped','login_expired','credential_error','source_mismatch'):raise
            if error.code in ('request_budget','task_budget'):self.optional_exhausted=True
            notes.append(label+'未取得（'+error.code+'），不冒充完整覆盖');return None

    async def metadata(self,bvid,budget):
        if not valid_bvid(bvid):raise BiliError('invalid_video','视频编号无效')
        meta=await self.client.api('/x/web-interface/view',{'bvid':bvid},budget=budget)
        if meta.get('bvid')!=bvid:raise BiliError('source_mismatch','视频元信息与所选编号不符')
        pages=meta.get('pages')
        if not isinstance(pages,list) or not pages:raise BiliError('video_unavailable','没有可读取的真实分P信息')
        return meta

    async def frames(self,bvid,cid,duration,budget,*,start=0,end=None,limit=16):
        end=duration if end is None else end
        data=await self.client.api('/x/player/videoshot',{'bvid':bvid,'cid':cid,'index':1},budget=budget)
        indices,images=data.get('index') or [],data.get('image') or []
        if not indices and isinstance(data.get('pvdata'),str):
            raw=await self.client.get(snapshot_url(data['pvdata']),kind='frame_index',budget=budget,cap=64*1024,json_body=False)
            if len(raw)%2 or len(raw)>60000:raise BiliError('frames_unavailable','快照时间索引无法核对')
            indices=[int.from_bytes(raw[offset:offset+2],'big') for offset in range(0,len(raw),2)]
        try:
            nx,ny=int(data['img_x_len']),int(data['img_y_len']);width,height=int(data['img_x_size']),int(data['img_y_size'])
            if not (1<=nx<=20 and 1<=ny<=20 and 1<=width<=640 and 1<=height<=640):raise ValueError()
            if not isinstance(indices,list) or not isinstance(images,list) or len(indices)>30000:raise ValueError()
            stamps=[float(value) for value in indices]
            if not stamps or stamps!=sorted(stamps) or any(not math.isfinite(value) or value<0 or value>duration+5 for value in stamps):raise ValueError()
        except (ValueError,KeyError,TypeError):raise BiliError('frames_unavailable','快照索引无法核对') from None
        wanted=[index for index,stamp in enumerate(stamps) if start<=stamp<end]
        wanted=spaced(wanted,64)
        sheets={}
        for index in wanted:
            sheet=index//(nx*ny)
            if sheet<len(images):sheets.setdefault(sheet,[]).append((index%(nx*ny),stamps[index]))
        # Spread bounded downloads across the whole window, not only its beginning.
        chosen_sheets=spaced(sorted(sheets),4);candidates=[]
        for index in chosen_sheets:
            url=snapshot_url(images[index])
            raw=await self.client.get(url,kind='frame',budget=budget,cap=2*1024**2,json_body=False)
            candidates.extend(await asyncio.to_thread(crop_frames,raw,sheets[index],nx,ny,width,height))
        selected,analysis=await asyncio.to_thread(select_frames,candidates,limit)
        return selected,{**analysis,'media_mode':'official_snapshot','snapshot_sheets_read':len(chosen_sheets),
            'snapshot_sheets_available':len(sheets),'sampling_limit':'稀疏官方快照，只能在实际候选之间估计变化，不能当作连续逐帧扫描'}

    @staticmethod
    def valid_comment(value):
        value=text(value,600)
        compact=re.sub(r'\s+','',value)
        if len(compact)<2:return ''
        if re.search(r'(?:加.{0,2}[群微薇]|私信.{0,5}(?:领取|赚钱|推广)|返现|刷单|代刷|日赚|点击.{0,4}链接)',compact):return ''
        if len(set(compact))<=1 and len(compact)>8:return ''
        return value

    async def comments(self,aid,bvid,budget,settings,notes):
        maximum=settings.bilibili_comments_limit;reply_threads=settings.bilibili_reply_threads
        root_limit=max(20,maximum-reply_threads*4);result=[];seen=set();signatures=set();cursor=0;raw_count=0;pages=0;replies_read=0;ended=False
        def add(row,parent=''):
            if not isinstance(row,dict):return
            identity=str(row.get('rpid') or row.get('rpid_str') or '')
            message=self.valid_comment((row.get('content') or {}).get('message'))
            key=re.sub(r'(.)\1{3,}',r'\1\1\1',re.sub(r'\s+','',message)).casefold()
            if not identity or not identity.isdigit() or not message or identity in seen or key in signatures:return
            seen.add(identity);signatures.add(key)
            result.append({'id':identity,'parent_id':parent,'text':message,'created':row.get('ctime',0),
                'source_url':'https://www.bilibili.com/video/'+bvid+'/?comment_on=1&comment_root_id='+(parent or identity)})
        for _ in range(settings.bilibili_comment_pages):
            value=await self.optional(self.client.api('/x/v2/reply/main',{'type':1,'oid':aid,'mode':3,'next':cursor},budget=budget),'评论分页',notes)
            if not value:break
            pages+=1;rows=value.get('replies') or [];raw_count+=len(rows)
            for row in rows:
                if len(result)>=root_limit:break
                add(row)
            info=value.get('cursor') or {};nxt=info.get('next')
            if info.get('is_end') is True or not rows:ended=True;break
            if len(result)>=root_limit or nxt is None or str(nxt)==str(cursor):break
            cursor=nxt
        roots=list(result)
        for root in roots[:reply_threads]:
            if len(result)>=maximum:break
            value=await self.optional(self.client.api('/x/v2/reply/reply',{'type':1,'oid':aid,'root':root['id'],'pn':1,'ps':min(10,maximum-len(result))},budget=budget),'楼中楼',notes)
            if not value:continue
            replies_read+=1;rows=value.get('replies') or [];raw_count+=len(rows)
            for row in rows:
                if len(result)>=maximum:break
                add(row,root['id'])
        return result,{'pages_read':pages,'reply_threads_read':replies_read,'raw_items':raw_count,'effective_items':len(result),
            'limit':maximum,'target_min':20,'target_met':len(result)>=20,'end_confirmed':ended,
            'complete_comments':False,'consensus_inferred':False}

    @staticmethod
    def reaction_tags(value):
        tags=[]
        for pattern,label in ((r'哈|笑|绷|乐','好笑'),(r'泪|哭|难受|心疼','共情'),(r'离谱|不是|啊[?？]|居然','惊讶'),
                              (r'[?？]|怎么|为什么','疑惑'),(r'懂|确实|一样|我也','共鸣')):
            if re.search(pattern,value):tags.append(label)
        return tags or ['其他表达']

    async def danmaku(self,cid,duration,start,end,budget,settings,notes):
        raw=await self.optional(self.client.get(API+'/x/v1/dm/list.so',params={'oid':cid},budget=budget,cap=512*1024,json_body=False),'弹幕',notes)
        if not raw:return [],{'available':0,'selected':0,'complete':False}
        if not raw.lstrip().startswith(b'<'):
            for mode in (zlib.MAX_WBITS,-zlib.MAX_WBITS):
                try:
                    decoder=zlib.decompressobj(mode);decoded=decoder.decompress(raw,512*1024)
                    if decoder.eof and not decoder.unconsumed_tail:raw=decoded;break
                except zlib.error:continue
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():notes.append('弹幕含不允许的XML结构，未读取');return [],{'available':0,'selected':0,'complete':False}
        try:root=ET.fromstring(raw)
        except ET.ParseError:notes.append('弹幕格式未确认，未冒充完整弹幕');return [],{'available':0,'selected':0,'complete':False}
        groups={};seen=set();available=0
        for node in root.iter('d'):
            try:stamp=float(node.attrib.get('p','').split(',')[0])
            except ValueError:continue
            value=self.valid_comment(node.text);fingerprint=re.sub(r'(.)\1{3,}',r'\1\1\1',re.sub(r'\s+','',value))
            if not math.isfinite(stamp) or not start<=stamp<min(end,duration+5) or not value:continue
            available+=1
            if fingerprint in seen:continue
            seen.add(fingerprint);tags=self.reaction_tags(value);bucket=int((stamp-start)/max(1,(end-start)/12))
            key=(bucket,tags[0]);groups.setdefault(key,[]).append({'time':stamp,'text':value,'tags':tags})
        chosen=[];keys=sorted(groups);offset=0
        while len(chosen)<settings.bilibili_danmaku_limit:
            added=False
            for key in keys:
                if offset<len(groups[key]):chosen.append(groups[key][offset]);added=True
                if len(chosen)>=settings.bilibili_danmaku_limit:break
            if not added:break
            offset+=1
        chosen.sort(key=lambda row:row['time'])
        return chosen,{'available_in_window':available,'distinct_in_window':len(seen),'selected':len(chosen),
            'temporal_buckets':12,'complete':False,'selection':'时间窗、表达差异与粗略情绪标签；标签不是观众真实心理定论'}

    async def read(self,bvid,budget,settings,*,metadata=None,page=None,start=0,config=None):
        self.optional_exhausted=False;meta=metadata or await self.metadata(bvid,budget)
        if meta.get('bvid')!=bvid:raise BiliError('source_mismatch','视频元信息与所选编号不符')
        pages=meta.get('pages') or []
        try:
            requested=int(page or 1)
            part=next((row for row in pages if isinstance(row,dict) and int(row.get('page') or 1)==requested),None)
        except (TypeError,ValueError):raise BiliError('invalid_part','分P目录或选择格式未确认') from None
        if not part:raise BiliError('invalid_part','所选分P不在真实目录中')
        try:
            cid,aid,duration=int(part['cid']),int(meta['aid']),float(part.get('duration') or meta['duration'])
            if cid<=0 or aid<=0 or not math.isfinite(duration) or duration<=0:raise ValueError()
            start=float(start)
            if not math.isfinite(start) or start<0 or start>=duration:raise ValueError()
        except (TypeError,ValueError,KeyError):raise BiliError('video_unavailable','视频身份、时长或阅读窗口无效') from None
        end=min(duration,start+settings.bilibili_segment_seconds);notes=[];segments=[];frames=[];frame_analysis={};comments=[];danmaku=[]
        player=await self.optional(self.client.api('/x/player/wbi/v2',{'bvid':bvid,'cid':cid},wbi=True,budget=budget),'字幕列表',notes)
        if player:
            for key,expected in (('bvid',bvid),('cid',cid),('aid',aid)):
                if player.get(key) is not None and str(player[key])!=str(expected):raise BiliError('source_mismatch','字幕身份与视频/分P不符')
            tracks=[row for row in (player.get('subtitle') or {}).get('subtitles',[]) if isinstance(row,dict) and isinstance(row.get('subtitle_url'),str) and row['subtitle_url'].strip()]
            tracks.sort(key=lambda row:not str(row.get('lan','')).startswith('zh'))
            if tracks:
                url=tracks[0]['subtitle_url'].strip();url='https:'+url if url.startswith('//') else url
                fetched=await self.optional(self.client.get(url,kind='subtitle',budget=budget),'字幕正文',notes)
                if fetched:segments=[row for row in subtitle_segments(fetched[0].get('body') or [],duration) if row['end']>start and row['start']<end]
        if not segments:notes.append('未取得本窗口有效字幕，没有本地ASR，不声称听懂音频')
        if settings.bilibili_visual_enabled:
            if config:
                candidates=await self.optional(media_frames(self.client,config,bvid,cid,start,end,budget),'合法低清媒体/FFmpeg',notes)
                if candidates:
                    rows,frame_analysis=candidates;frames,analysis=await asyncio.to_thread(select_frames,rows,settings.bilibili_keyframes_limit);frame_analysis.update(analysis)
            if not frames or frame_analysis.get('decoder_completed') is False:
                attempt=dict(frame_analysis) if frames else None
                if attempt:notes.append('轻量解码未完成，只取得局部帧；尝试官方快照覆盖整个窗口，未取得部分不冒充理解')
                fetched=await self.optional(self.frames(bvid,cid,duration,budget,start=start,end=end,limit=settings.bilibili_keyframes_limit),'动态快照选帧',notes)
                if fetched:
                    frames,frame_analysis=fetched
                    if attempt:frame_analysis['ffmpeg_attempt']=attempt
        discussion_coverage={};danmaku_coverage={}
        if settings.bilibili_comments_enabled:
            fetched=await self.optional(self.comments(aid,bvid,budget,settings,notes),'评论讨论',notes)
            if fetched:comments,discussion_coverage=fetched
        if settings.bilibili_danmaku_enabled:
            fetched=await self.optional(self.danmaku(cid,duration,start,end,budget,settings,notes),'分场景弹幕',notes)
            if fetched:danmaku,danmaku_coverage=fetched
        for row in danmaku:
            closest=min((frame['time'] for frame in frames),key=lambda value:abs(value-row['time'])) if frames else None
            row['scene_time']=closest if closest is not None and abs(closest-row['time'])<=10 else None
            row['scene_alignment']='nearest_actual_sample_within_10_seconds' if row['scene_time'] is not None else 'unmapped'
        boards=await asyncio.to_thread(storyboards,frames)
        clipped=[{'start':max(start,row['start'])-start,'end':min(end,row['end'])-start} for row in segments]
        window_fraction=subtitle_fraction(clipped,end-start)
        subtitle_gaps=[];cursor=start
        for row in segments:
            left,right=max(start,row['start']),min(end,row['end'])
            if left>cursor:subtitle_gaps.append([cursor,left])
            cursor=max(cursor,right)
        if cursor<end:subtitle_gaps.append([cursor,end])
        return {'bvid':bvid,'cid':cid,'aid':aid,'page':int(part.get('page') or 1),'part':text(part.get('part'),120),
            'title':text(meta.get('title'),200),'author':text((meta.get('owner') or {}).get('name'),80),'duration':duration,
            'url':'https://www.bilibili.com/video/'+bvid+'/?p='+str(part.get('page') or 1),
            'segments':segments,'frames':frames,'boards':boards,'comments':comments,'danmaku':danmaku,
            'coverage':{'state':'partial' if segments or frames else 'unconfirmed','segment_start':start,'segment_end':end,
                'subtitle_segments':len(segments),'subtitle_timed_fraction':round(window_fraction*(end-start)/duration,4),
                'window_subtitle_fraction':window_fraction,'sampled_frame_times':[frame['time'] for frame in frames],
                'window_without_subtitles':subtitle_gaps,
                'frame_selection':frame_analysis,'board_count':len(boards),'visual_understood':False,
                'comment_count':len(comments),'comments':discussion_coverage,'danmaku_count':len(danmaku),'danmaku':danmaku_coverage,
                'comment_time_alignment':'全视频讨论，未给定时间的评论不能强行对应当前窗口的某一帧',
                'unread_time_ranges':([[0,start]] if start>0 else [])+subtitle_gaps+([[end,duration]] if end<duration else []),
                'unread_range_basis':'未进入本轮窗口或没有实际字幕覆盖的区间；另列的采样画面点不代表连续播放或逐时理解',
                'semantic_verification':'unverified','complete_playback':False,'notes':notes}}
