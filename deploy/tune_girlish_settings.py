"""One-time deploy tuning script for girlish persona, expressive temperature and token limits.

Configures:
- chat_short_reply_chars = 80
- persona_density = 'normal'
- teasing = 'normal'
- sticker_usage_target = 45
- max_tokens = 400
- model_temperature = 0.95 across active settings and saved model cards.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import urllib.request

DEFAULT_DATA_DIR = Path('/var/lib/whalegirl-linux/core')
DEFAULT_API_BASE = 'http://127.0.0.1:8092'

SETTINGS_UPDATES = {
    'reply_max_parts': 3,
    'chat_short_reply_chars': 56,
    'chat_short_reply_sentences': 2,
    'persona_density': 'normal',
    'teasing': 'normal',
    'sticker_usage_target': 45,
    'max_tokens': 400,
    'model_temperature': 0.95,
}

TARGET_TEMPERATURE = 0.95


def tune_file_config(data_dir: Path) -> dict:
    """Tune configuration directly on disk using Config or raw JSON."""
    data_dir = Path(data_dir).resolve()
    if not data_dir.exists():
        raise FileNotFoundError(f'Data directory not found: {data_dir}')

    settings_file = data_dir / 'settings.json'
    profiles_file = data_dir / 'model-profiles.json'

    before_settings = {}
    after_settings = {}
    if settings_file.exists():
        before_settings = json.loads(settings_file.read_text('utf-8'))
        after_settings = {**before_settings, **SETTINGS_UPDATES}
        settings_file.write_text(json.dumps(after_settings, ensure_ascii=False, indent=2), 'utf-8')

    before_profiles = {}
    after_profiles = {}
    updated_profile_ids = []
    if profiles_file.exists():
        before_profiles = json.loads(profiles_file.read_text('utf-8'))
        if isinstance(before_profiles, dict) and isinstance(before_profiles.get('profiles'), dict):
            inner = {}
            for pid, card in before_profiles['profiles'].items():
                if isinstance(card, dict):
                    updated_card = dict(card)
                    updated_card['model_temperature'] = TARGET_TEMPERATURE
                    updated_card['max_tokens'] = 400
                    inner[pid] = updated_card
                    updated_profile_ids.append(pid)
                else:
                    inner[pid] = card
            after_profiles = {**before_profiles, 'profiles': inner}
        else:
            for pid, card in before_profiles.items():
                if isinstance(card, dict):
                    updated_card = dict(card)
                    updated_card['model_temperature'] = TARGET_TEMPERATURE
                    updated_card['max_tokens'] = 400
                    after_profiles[pid] = updated_card
                    updated_profile_ids.append(pid)
                else:
                    after_profiles[pid] = card
        profiles_file.write_text(json.dumps(after_profiles, ensure_ascii=False, indent=2), 'utf-8')

    report = {
        'mode': 'disk',
        'data_dir': str(data_dir),
        'updated_settings': {k: after_settings.get(k) for k in SETTINGS_UPDATES},
        'updated_profiles': updated_profile_ids,
        'target_temperature': TARGET_TEMPERATURE,
    }
    return report


def tune_api_config(api_base: str, token: str) -> dict:
    """Tune configuration through running admin HTTP API."""
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }

    req = urllib.request.Request(f'{api_base}/api/config', headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        cur_settings = json.load(resp)

    new_settings = {**cur_settings, **SETTINGS_UPDATES}
    req_update = urllib.request.Request(
        f'{api_base}/api/config',
        headers=headers,
        data=json.dumps({'settings': new_settings}).encode('utf-8'),
    )
    with urllib.request.urlopen(req_update, timeout=10) as resp:
        saved_settings = json.load(resp)

    # Update model profiles
    req_profiles = urllib.request.Request(f'{api_base}/api/model-profiles', headers=headers)
    with urllib.request.urlopen(req_profiles, timeout=10) as resp:
        profiles_data = json.load(resp)

    updated_profiles = []
    for pid, profile in profiles_data.items():
        if isinstance(profile, dict):
            patched = dict(profile)
            patched['model_temperature'] = TARGET_TEMPERATURE
            body = json.dumps({'profile_id': pid, 'profile': patched}).encode('utf-8')
            put_req = urllib.request.Request(f'{api_base}/api/model-profiles', headers=headers, data=body)
            with urllib.request.urlopen(put_req, timeout=10) as resp:
                pass
            updated_profiles.append(pid)

    return {
        'mode': 'api',
        'api_base': api_base,
        'updated_settings': {k: saved_settings.get(k) for k in SETTINGS_UPDATES},
        'updated_profiles': updated_profiles,
        'target_temperature': TARGET_TEMPERATURE,
    }


def main():
    parser = argparse.ArgumentParser(description='Tune girlish settings and temperatures')
    parser.add_argument('--data-dir', type=Path, default=None, help='Target core data directory')
    parser.add_argument('--api-base', type=str, default=None, help='Target admin API URL')
    parser.add_argument('--token', type=str, default='', help='Admin bearer token for API mode')
    args = parser.parse_args()

    if args.api_base:
        token = args.token
        if not token and args.data_dir:
            token_path = args.data_dir / 'admin.token'
            if token_path.exists():
                token = token_path.read_text('utf-8').strip()
        report = tune_api_config(args.api_base, token)
    else:
        data_dir = args.data_dir or (DEFAULT_DATA_DIR if DEFAULT_DATA_DIR.exists() else Path.cwd())
        report = tune_file_config(data_dir)

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
