"""Small, provenance-bound video evidence reads; no playback or platform writes."""
import asyncio
import bisect
import html
import io
import math
import re
import xml.etree.ElementTree as ET

from PIL import Image

from .bilibili_client import API, BiliError


def text(value, cap=500):
    return html.unescape(re.sub('<[^>]*>', '', str(value or ''))).strip()[:cap]


def valid_bvid(value):
    return isinstance(value, str) and re.fullmatch(r'BV[A-Za-z0-9]{10}', value) is not None


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
    def __init__(self, client):
        self.client = client

    async def optional(self, operation, label, notes):
        try:
            return await operation
        except BiliError as exc:
            if exc.code in ('risk_paused', 'access_stopped', 'login_expired', 'credential_error',
                            'request_budget', 'task_budget', 'source_mismatch'):
                raise
            notes.append(label+'未取得（'+exc.code+'）')
            return None

    async def frames(self, bvid, cid, duration, budget):
        data = await self.client.api('/x/player/videoshot', {'bvid': bvid, 'cid': cid, 'index': 1}, budget=budget)
        indices, images = data.get('index') or [], data.get('image') or []
        try:
            x_count, y_count = int(data['img_x_len']), int(data['img_y_len'])
            width, height = int(data['img_x_size']), int(data['img_y_size'])
            if not (1 <= x_count <= 20 and 1 <= y_count <= 20 and 1 <= width <= 640 and 1 <= height <= 640):
                raise ValueError()
            if not isinstance(indices, list) or not isinstance(images, list) or len(indices) > 30000:
                raise ValueError()
            indices = [float(stamp) for stamp in indices]
            if not indices or any(not math.isfinite(stamp) or stamp < 0 or stamp > duration+5 for stamp in indices) or indices != sorted(indices):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise BiliError('frames_unavailable', '未取得可定位的视频快照') from None
        chosen = sorted({max(0, bisect.bisect_right(indices, duration*f)-1) for f in (.12, .4, .67, .9)})
        groups = {}
        for index in chosen:
            sheet = index//(x_count*y_count)
            if sheet < len(images):
                groups.setdefault(sheet, []).append((index % (x_count*y_count), indices[index]))
        frames = []
        for sheet, positions in list(groups.items())[:2]:
            url = str(images[sheet])
            url = 'https:'+url if url.startswith('//') else url
            raw = await self.client.get(url, kind='frame', budget=budget, cap=2*1024*1024, json_body=False)
            try:
                frames.extend(await asyncio.to_thread(crop_frames, raw, positions, x_count, y_count, width, height))
            except (OSError, Image.DecompressionBombError):
                raise BiliError('frames_unavailable', '视频快照无法安全解码') from None
        return frames[:4]

    async def read(self, bvid, budget, settings):
        if not valid_bvid(bvid):
            raise BiliError('invalid_video', '视频编号无效')
        meta = await self.client.api('/x/web-interface/view', {'bvid': bvid}, budget=budget)
        if meta.get('bvid') != bvid:
            raise BiliError('source_mismatch', '视频元信息与所选编号不符，已停止本轮学习')
        pages = meta.get('pages') or []
        if not pages or not isinstance(pages[0], dict):
            raise BiliError('video_unavailable', '视频没有可读取的分P信息')
        part = pages[0]
        try:
            cid, aid, duration = int(part['cid']), int(meta['aid']), float(part.get('duration') or meta['duration'])
            if cid <= 0 or aid <= 0 or not math.isfinite(duration) or duration <= 0:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise BiliError('video_unavailable', '视频身份或时长无效') from None
        notes, segments, frames, comments, danmaku = [], [], [], [], []
        player = await self.optional(self.client.api('/x/player/wbi/v2', {'bvid': bvid, 'cid': cid},
            wbi=True, budget=budget), '字幕列表', notes)
        if player:
            for key, expected in (('bvid', bvid), ('cid', cid), ('aid', aid)):
                if player.get(key) is not None and str(player[key]) != str(expected):
                    raise BiliError('source_mismatch', '字幕来源身份与视频不符，已停止本轮学习')
            tracks = [row for row in (player.get('subtitle') or {}).get('subtitles', []) if
                      isinstance(row, dict) and isinstance(row.get('subtitle_url'), str) and row['subtitle_url'].strip()]
            tracks.sort(key=lambda row: (not str(row.get('lan', '')).startswith('zh'), 'zh' not in str(row.get('lan', ''))))
            if tracks:
                url = tracks[0]['subtitle_url'].strip()
                url = 'https:'+url if url.startswith('//') else url
                fetched = await self.optional(self.client.get(url, kind='subtitle', budget=budget), '字幕正文', notes)
                if fetched:
                    segments = subtitle_segments(fetched[0].get('body') or [], duration)
        if not segments:
            notes.append('没有取得可用字幕，不能据此断言视频没有字幕或语音')
        if settings.bilibili_visual_enabled:
            frames = await self.optional(self.frames(bvid, cid, duration, budget), '抽样画面', notes) or []
        if settings.bilibili_comments_enabled:
            replies = await self.optional(self.client.api('/x/v2/reply/main', {'type': 1, 'oid': aid, 'mode': 3, 'next': 0},
                budget=budget), '评论', notes)
            if replies:
                comments = [text((row.get('content') or {}).get('message'), 220) for row in
                            (replies.get('replies') or [])[:8] if isinstance(row, dict)]
        if settings.bilibili_danmaku_enabled:
            raw = await self.optional(self.client.get(API+'/x/v1/dm/list.so', params={'oid': cid},
                budget=budget, cap=256*1024, json_body=False), '弹幕', notes)
            if raw and b'<!DOCTYPE' not in raw.upper() and b'<!ENTITY' not in raw.upper():
                try:
                    root = ET.fromstring(raw)
                    entries = []
                    for row in root.iter('d'):
                        stamp = float(row.attrib.get('p', '').split(',')[0])
                        if math.isfinite(stamp) and 0 <= stamp <= duration+5:
                            entries.append({'time': stamp, 'text': text(row.text, 100)})
                    danmaku = spaced(entries, 24)
                except (ET.ParseError, ValueError):
                    notes.append('弹幕格式未确认，未当作视频正文')
        return {'bvid': bvid, 'cid': cid, 'aid': aid, 'page': int(part.get('page') or 1),
            'title': text(meta.get('title'), 200), 'author': text((meta.get('owner') or {}).get('name'), 80),
            'part': text(part.get('part'), 120), 'duration': duration,
            'url': 'https://www.bilibili.com/video/'+bvid+'/', 'segments': segments,
            'comments': [value for value in comments if value], 'danmaku': danmaku, 'frames': frames,
            'coverage': {'state': 'partial' if segments or frames else 'unconfirmed',
                'subtitle_segments': len(segments), 'subtitle_timed_fraction': subtitle_fraction(segments, duration),
                'sampled_frame_times': [frame['time'] for frame in frames], 'visual_understood': False,
                'comment_count': len(comments), 'danmaku_count': len(danmaku),
                'semantic_verification': 'unverified', 'complete_playback': False, 'notes': notes}}
