"""Local UI fixture only; no background tasks or external gateway."""
import asyncio
import io
import json
import sys
import time
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from whalegirl_linux.app import create_app
from whalegirl_linux.settings import Settings


class Gateway:
    login = {}
    inflight_sends = 0
    ui_jobs_directory = None
    ui_lock = asyncio.Lock()
    async def chats(self):
        return [{'id':'preview@chatroom','name':'临时测试','isGroup':True}]
    async def status(self):
        return {'status':'disconnected'}
    async def close(self): pass


if __name__=='__main__':
    data=Path(sys.argv[1])
    app=create_app(data,gateway=Gateway(),run_background=False)
    app.state.config.save(Settings(account_id='preview',allowed_chat_ids=['preview@chatroom'],tieba_enabled='--controls' in sys.argv,
        tieba_share_chat_ids=['preview@chatroom']))
    if '--controls' in sys.argv:
        service=app.state.runtime.tieba
        service.learn('preview',{'tid':'987','text':'摸鱼是物种优势','url':'https://tieba.baidu.com/p/987','posts':[]},
                      {'memes':[{'phrase':'摸鱼是物种优势','meaning':'给摸鱼找借口','scene':'轻松聊天时调侃'}]})
        service.execute('''INSERT INTO tieba_shares(id,account,tid,chat,text,state,created,error)
            VALUES(?,?,?,?,?,'failed',?,?)''',('preview-share','preview','987','preview@chatroom',
            '这帖子挺有趣\nhttps://tieba.baidu.com/p/987',time.time(),'目标会话不在当前列表，尚未发送'))
    if '--archive' in sys.argv:
        from PIL import Image
        archive = app.state.runtime.tieba.archives
        text = '  这是一段原文\n\n保留换行与空格  \n' + '长内容不会被摘要替换 ' * 160 + '\n原文结束  '
        picture = {'id': '0'*24, 'pid': 101, 'url': 'https://tiebapic.baidu.com/a.gif',
                   'post_url': 'https://tieba.baidu.com/p/123?pid=101', 'original_available': True}
        source = {'tid': '123', 'forum': '绝区零内鬼', 'title': '原文收藏演示', 'url': 'https://tieba.baidu.com/p/123',
                  'text': text, 'source_version': 2, 'images': [picture],
                  'posts': [{'pid': '101', 'floor': 1, 'author': '公开作者', 'text': text,
                             'url': picture['post_url'], 'comments': []}],
                  'coverage': {'main_floor_present': True, 'posts_read': 1, 'has_more': True}}
        identity = archive.add(source, '有趣的长图文')
        archive.persist_source(archive.row(identity), source)
        output = io.BytesIO()
        Image.new('RGB', (24, 24), 'green').save(output, format='GIF')
        (archive.directory(archive.row(identity))/(picture['id']+'.bin')).write_bytes(output.getvalue())
        archive.execute("UPDATE tieba_archive_images SET state='saved',mime='image/gif',bytes=? WHERE archive_id=?", (len(output.getvalue()), identity))
        archive.execute("UPDATE tieba_archives SET state='complete' WHERE id=?", (identity,))
        archive.execute('''INSERT INTO tieba_posts(account,tid,forum,title,url,payload,seen)
            VALUES(?,?,?,?,?,?,?)''', ('preview', '123', source['forum'], source['title'], source['url'], json.dumps(source, ensure_ascii=False), time.time()))
    uvicorn.run(app,host='127.0.0.1',port=18102,log_level='error')
