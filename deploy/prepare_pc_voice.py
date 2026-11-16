"""Prepare one verified reference clip and an isolated local inference config.

No weight deserialization, bulk extraction, model download or existing config edit.
Run on the owner's Windows computer.
"""
from pathlib import Path
import io
import json
import secrets
import wave
import xml.etree.ElementTree as ET
import zipfile


def prepare(assets=Path('D:/appData/qqbot/voise'), tts=Path('D:/yachiyo/voice')):
    runtime = assets / 'runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    reference_id = 'bd366ec4020e73cb.wav'
    with zipfile.ZipFile(next(assets.glob('*.zip'))) as archive:
        workbook = next(n for n in archive.namelist() if n.endswith('.xlsx'))
        with zipfile.ZipFile(io.BytesIO(archive.read(workbook))) as book:
            ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            strings = [''.join(t.text or '' for t in e.findall('.//m:t', ns))
                       for e in ET.fromstring(book.read('xl/sharedStrings.xml')).findall('m:si', ns)]
            transcript = None
            for row in ET.fromstring(book.read('xl/worksheets/sheet1.xml')).findall('.//m:row', ns):
                values = {}
                for cell in row.findall('m:c', ns):
                    value = cell.find('m:v', ns)
                    value = value.text if value is not None else ''
                    values[cell.get('r')[0]] = strings[int(value)] if cell.get('t') == 's' else value
                if values.get('C') == reference_id:
                    transcript = values['D']
                    break
            assert transcript, 'Reference transcript not found'
        choices = [n for n in archive.namelist() if n.endswith('/' + reference_id)]
        assert len(choices) == 1, 'Reference filename is ambiguous'
        info = archive.getinfo(choices[0])
        assert 1000 < info.file_size < 10 * 1024**2
        data = archive.read(info)
        with wave.open(io.BytesIO(data)) as wav:
            seconds = wav.getnframes() / wav.getframerate()
            assert 3 <= seconds <= 15, 'Use a short, clean reference'
        (runtime / 'reference.wav').write_bytes(data)
    custom = {
        'version': 'v2ProPlus', 'device': 'cuda', 'is_half': True,
        'bert_base_path': str(tts / 'GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large'),
        'cnhuhbert_base_path': str(tts / 'GPT_SoVITS/pretrained_models/chinese-hubert-base'),
        't2s_weights_path': str(assets / 'GPT-weights/CyreneV3.7-e25.ckpt'),
        'vits_weights_path': str(assets / 'SoVITS-Weights/CyreneV3.7_e16_s1392.pth'),
    }
    assert all(Path(value).exists() for key, value in custom.items() if key.endswith('_path'))
    (runtime / 'cyrene.yaml').write_text(json.dumps({'custom': custom}, indent=2), encoding='utf-8')
    config_path = runtime / 'bridge.json'
    if not config_path.exists():
        value = {'token': secrets.token_urlsafe(48), 'upstream': 'http://127.0.0.1:9881',
                 'presets': {'cyrene': {'ref_audio_path': str(runtime / 'reference.wav'),
                                        'prompt_text': transcript, 'prompt_lang': 'zh'}},
                 'timeout': 180, 'max_bytes': 16 * 1024**2, 'max_text': 600}
        config_path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'reference_seconds': round(seconds, 3), 'reference_bytes': len(data),
                      'model': custom['version'], 'config': str(runtime / 'cyrene.yaml')}))


if __name__ == '__main__':
    prepare()
