"""Apply the administrator-authorized, manually curated chat style reference."""
import json
from pathlib import Path
import urllib.request

NOTE=('群聊表情是回应本身，不只是文字后的装饰。轻松接梗、被调侃、收到表情或拍一拍时，先想一张贴切表情是否已能接住；'
      '能就只发它，不凑一句哈哈、我懂或人设自称来解释。需要说具体事时用一两句短话，表情可放开头、中间或末尾，不固定两句加一张。'
      '连续轻松互动中可以更常用表情；不硬把普通表情变成识图点评，也不为提高频率刷屏。'
      '保持温柔可爱，玩笑时俏皮，认真时体贴；借鉴群友的节奏，不照搬冷嘲、攻击或过度亲密。没有合适表情时正常说话，不编造表情ID。')
VALUES={'sticker_text_pair_eagerness':95,'sticker_only_eagerness':90,'sticker_reaction_eagerness':95,
        'sticker_max_per_reply':1,'sticker_style_note':NOTE}


def main():
    root=Path('/var/lib/whalegirl-linux/core')
    headers={'Authorization':'Bearer '+(root/'admin.token').read_text().strip(),'Content-Type':'application/json'}
    def api(values=None):
        request=urllib.request.Request('http://127.0.0.1:8092/api/config',headers=headers,
            data=json.dumps({'settings':values}).encode() if values is not None else None)
        return json.load(urllib.request.urlopen(request,timeout=12))
    before=api();after=api(VALUES)
    changed={key for key in set(before)|set(after) if before.get(key)!=after.get(key)}
    if changed-set(VALUES) or any(after.get(k)!=v for k,v in VALUES.items()):raise RuntimeError('Unexpected preference update')
    report=Path('/opt/whalegirl-linux-assets/sticker-preferences-deployment.json')
    data=json.loads(report.read_text());data['manual_preferences']={k:v for k,v in VALUES.items() if k!='sticker_style_note'}
    data['curated_note_chars']=len(NOTE);data['other_settings_preserved']=True
    report.write_text(json.dumps(data,ensure_ascii=False,indent=2))
    print(json.dumps({'preferences':data['manual_preferences'],'curated_note_chars':len(NOTE),'other_settings_preserved':True},ensure_ascii=False))


if __name__=='__main__':main()
