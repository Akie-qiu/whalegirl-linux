"""Explicit, reviewable style-only revision; never writes a live persona.

The existing replay harness may load ``patch`` as a standalone module. It uses
only the standard library and requires the known sections before making edits.
All identity, relationship, interests and world-building sections stay verbatim.
"""
import hashlib
import re


SECTIONS = re.compile(r'^【[^\n】]+】', re.M)


def patch(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Expected a complete persona')
    parts = SECTIONS.split(text)
    headings = SECTIONS.findall(text)
    if not headings:
        raise ValueError('Persona sections were not found')
    targets = {
        'pauses': next((h for h in headings if h.startswith(('【说话的省略号', '【停顿与语气'))), None),
        'examples': next((h for h in headings if h.startswith('【语气参考')), None),
        'check': '【回复前的小自检】' if '【回复前的小自检】' in headings else None,
    }
    if not all(targets.values()):
        raise ValueError('Persona style anchors changed; review before revising')
    changed = []
    output = [parts[0]]
    for heading, body in zip(headings, parts[1:]):
        new_heading, new_body = heading, body
        if heading == targets['pauses']:
            new_heading = '【停顿与语气】'
            new_body = '\n• 短句顺着当下语气说，不要求每句都带省略号、语气词或小动作。真正迟疑或欲言又止时自然停顿；温柔也可以干净利落。\n'
        elif heading == targets['examples']:
            # These examples were forcing pauses and unsolicited bedtime advice.
            # Keep the other examples and their introductory cautions intact.
            lines = body.splitlines(keepends=True)
            lines = [line for line in lines if not (line.lstrip().startswith('• 关心人时：')
                     and any(word in line for word in ('早点休息', '早点睡', '快睡')))]
            lines = [line for line in lines if not line.lstrip().startswith(('- 主人赌气说', '- 关心累了的人时'))]
            new_body = re.sub(r'(?:…|\.{3,}|。{3,})+', '，', ''.join(lines))
            new_body = re.sub(r'([“\"（(])，|，(?=[”\"）)。！？!?])', lambda m: m.group(1) or '', new_body)
        elif heading == targets['check']:
            new_body = ('\n默默判断，不输出推理过程：\n'
                        '• 这句话在对谁说、承接什么，我是否有具体想表达的内容？不相关又没有新内容时可以旁观。\n'
                        '• 有没有接住实际事情和关系，而不是又用同一种认错、撒娇、关心或追问填空？\n'
                        '• 是否编造了没发生的经历、观测或对方的动机？只按实际依据表达。\n'
                        '• 人设由连续的选择自然体现，不必每轮补一个标志性动作；普通闲聊仍简短，技术问题说清楚。\n')
        elif heading == '【情绪反应图】':
            new_body = body.replace(
                '• 对方熬夜或不吃饭：嘴上嘀咕两句，其实是认真地催人去休息、去吃饭。',
                '• 对方主动提到困倦或没吃饭时，按需要关心，不因时间晚自动催睡；对方不想被提醒就停止提醒，仍可聊别的。')
        if new_heading != heading or new_body != body:
            changed.append(heading)
        output.extend((new_heading, new_body))
    result = ''.join(output)
    if len(result) > 16000:
        raise ValueError('Revised persona exceeds the supported size')
    report = {'original_sha256': hashlib.sha256(text.encode()).hexdigest(),
              'result_sha256': hashlib.sha256(result.encode()).hexdigest(),
              'changed_sections': changed, 'preserved_sections': [h for h in headings if h not in changed],
              'original_chars': len(text), 'new_chars': len(result), 'all_anchors_found': True}
    return result, report
