"""A single bounded recovery for malformed model replies after tool execution."""
import json
import re
import time

from .stickers import parse_json


class ToolReplyFormatError(ValueError):
    def __init__(self, stage, kind):
        self.stage=stage
        super().__init__('模型后续回复格式无效（'+stage+'/'+kind+'）')


def decision(raw):
    value=parse_json(raw)
    if not isinstance(value,dict):
        raise ValueError('Reply decision must be an object')
    return value


async def guarded_chat(engine,messages,*,diagnostics,stage,**options):
    """No additional model request on valid output; never execute a tool here."""
    options={**options,'response_format':{'type':'json_object'}}
    started=time.monotonic()
    raw=await engine.chat(messages,**options)
    attempt={'stage':stage,'seconds':round(time.monotonic()-started,3),
             'reply_chars':len(raw or ''),'ok':False}
    diagnostics.setdefault('tool_reply_stages',[]).append(attempt)
    try:
        value=decision(raw)
    except (TypeError,ValueError) as exc:
        error={'stage':stage,'type':type(exc).__name__,'reply_chars':len(raw or ''),
               'reply_starts_json':str(raw or '').lstrip().startswith(('{','['))}
        diagnostics['format_error']=error
        code=re.match(r'HTTP \d{3}',str(getattr(engine,'last_error','')))
        if (not raw and code) or diagnostics.get('format_repair_attempts',0)>=1:
            if code:error['upstream_status']=code[0]
            raise ToolReplyFormatError(stage,type(exc).__name__) from exc
        diagnostics['format_repair_attempts']=diagnostics.get('format_repair_attempts',0)+1
        repair_messages=[*messages,*([{'role':'assistant','content':str(raw)[:12000]}] if raw else []),
            {'role':'user','content':(
                '刚才的模型回复不是有效JSON。仅修复本次工具结果之后的回复格式，输出完整JSON对象，不要输出代码块。'
                '必须据上面已经执行的工具实际结果回答，不重新执行或重复提交已经执行的工具；不新增记忆修改、收藏、分享或生图任务。'
                '工具没有找到图片就不能声称已找到或已发送，也不能换成生图。保留原有回答意图，按既定格式填写actions等字段。'
                '未执行的图片选择仍可从已给出的候选中选；工具已经返回结果时不要重复该请求。')}]
        repair_options={**options,'operation':'tool_followup'}
        repaired=await engine.chat(repair_messages,**repair_options)
        try:
            value=decision(repaired)
        except (TypeError,ValueError) as repair_error:
            attempt['repair_ok']=False
            diagnostics['format_error']['repair_type']=type(repair_error).__name__
            raise ToolReplyFormatError(stage,type(repair_error).__name__) from repair_error
        attempt['repair_ok']=True
        diagnostics['format_recovery']=stage
    attempt['ok']=True
    return json.dumps(value,ensure_ascii=False)
