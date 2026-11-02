"""Per-image aspect choice; explicit pixel sizes use the configured long edge."""
from __future__ import annotations

import re

RATIOS = ('1:1', '4:3', '3:4', '16:9', '9:16', '3:2', '2:3', '4:5', '5:4', '2:1', '1:2')
CANVAS_RULES = ('同时返回image_aspect_ratio，按本次画面和分镜自主选择1:1、4:3、3:4、16:9、9:16、'
                '3:2、2:3、4:5、5:4、2:1或1:2。方格漫画可选方图，横向互动可选横图，纵向分镜可选竖图，'
                '不要每次固定方图；具体尺寸由服务器按后台分辨率换算，不把尺寸和比例写进朋友圈文案。')


def canvas(base, aspect='auto'):
    if aspect not in ('auto', '', *RATIOS):
        raise ValueError('无效图片比例')
    if not re.fullmatch(r'auto|[0-9]{3,4}x[0-9]{3,4}', str(base)):
        raise ValueError('无效基础分辨率')
    if aspect in ('auto', ''):
        return {'base_size': base, 'aspect_ratio': 'auto', 'size': base}
    edge = max(map(int, base.split('x'))) if base != 'auto' else 1024
    a, b = map(int, aspect.split(':'))
    # Eight-pixel alignment keeps common image backends compatible while
    # preserving the long edge (4096 for the requested 4K configuration).
    width = edge if a >= b else max(8, round(edge * a / b / 8) * 8)
    height = edge if b >= a else max(8, round(edge * b / a / 8) * 8)
    return {'base_size': base, 'aspect_ratio': aspect, 'size': f'{width}x{height}'}


def reference_required(plan):
    return bool(plan.get('inputs') or plan.get('sources') or plan.get('character'))
