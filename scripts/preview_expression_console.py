"""Local console fixture: synthetic contacts, no WeChat or model connection."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from whalegirl_linux.app import create_app
from whalegirl_linux.settings import Settings
import uvicorn


class PreviewGateway:
    login = {}
    async def status(self):
        return {'status': 'logged_in', 'loggedInUser': 'preview-account'}

    async def chats(self):
        return [{'id': 'preview@chatroom', 'name': '界面验收群', 'isGroup': True}]

    async def messages(self, *args, **kwargs):
        return []

    async def close(self):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18112)
    args = parser.parse_args()
    data = ROOT / 'artifacts/expression-console-20261103/preview-data'
    data.mkdir(parents=True, exist_ok=True)
    (data / 'admin.token').write_text('preview-local-only', 'utf-8')
    app = create_app(data, gateway=PreviewGateway(), run_background=False)
    if not (data / 'settings.json').exists():
        app.state.config.save(Settings(enabled=False, account_id='preview-account', allowed_chat_ids=['preview@chatroom'],
            autonomy_enabled=True, experience_growth_enabled=True, chat_habits_enabled=True,
            girlish_tone='bright', reply_warmth='normal', teasing='low', banter_level='normal', play_along='high',
            chat_short_replies_enabled=True, chat_short_reply_sentences=2, chat_short_reply_chars=40,
            system_prompt_override='界面验收使用的合成角色卡。', image_character_variant='girl'))
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning', access_log=False)


if __name__ == '__main__':
    main()
