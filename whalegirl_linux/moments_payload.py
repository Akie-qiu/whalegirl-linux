"""Parse the native SNS cache without exposing raw XML or media credentials."""
from __future__ import annotations

import xml.etree.ElementTree as ET


def number(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def positive_id(*values):
    return next((str(v) for v in values if str(v or '').isdigit() and int(v) > 0), '0')


def parse_post(row, own_ids=()):
    raw = row.get('content') or ''
    if not isinstance(raw, str) or len(raw) > 2_000_000 or '<!DOCTYPE' in raw.upper():
        return None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    timeline = root if root.tag == 'TimelineObject' else root.find('TimelineObject')
    if timeline is None:
        return None
    identity = timeline.findtext('id') or str(number(row.get('tid')) & ((1 << 64) - 1))
    if not identity.isdigit() or not int(identity):
        return None
    extra = root.find('LocalExtraInfo')
    author = timeline.findtext('username') or row.get('user_name') or ''
    comments = []
    if extra is not None:
        for item in extra.findall('.//user_comment'):
            if number(item.findtext('b_deleted')):
                continue
            text = item.findtext('content') or item.findtext('contentDesc') or ''
            cid = positive_id(item.findtext('comment_64id'), item.findtext('comment_id'))
            if not text.strip() or not cid.isdigit() or not int(cid):
                continue
            comments.append({
                'id': cid, 'author': item.findtext('username') or '',
                'name': item.findtext('nickname') or '', 'text': text[:2000],
                'created': number(item.findtext('create_time')),
                'reply_to': positive_id(item.findtext('ref_comment_64id'), item.findtext('ref_comment_id')),
                'target': item.findtext('ref_username') or item.findtext('to_username') or '',
                'own': (item.findtext('username') or '') in set(own_ids),
                'local': bool(number(item.findtext('is_local_added'))),
            })
    return {
        'id': identity, 'author': author,
        'name': extra.findtext('nickname', '') if extra is not None else '',
        'own': author in set(own_ids), 'created': number(timeline.findtext('createTime')),
        'text': (timeline.findtext('contentDesc') or '')[:4000],
        'title': (timeline.findtext('ContentObject/title') or '')[:300],
        'kind': number(timeline.findtext('ContentObject/type')),
        'image_count': len(timeline.findall('ContentObject/mediaList/media')),
        'comments': comments[-60:],
    }


def parse_notification(row, own_ids=()):
    cid = str(row.get('comment64_id') or row.get('comment_id') or '')
    if not cid.isdigit() or not int(cid) or not str(row.get('content') or '').strip():
        return None
    author = row.get('from_username') or ''
    if author in set(own_ids):
        return None
    return {
        'id': cid, 'post_id': str(number(row.get('feed_id')) & ((1 << 64) - 1)),
        'author': author, 'name': row.get('from_nickname') or '',
        'text': str(row['content'])[:2000], 'created': number(row.get('create_time')),
        'reply_to': '0', 'target': row.get('to_username') or '',
    }
