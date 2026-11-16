"""Convert the owner's Xiaofei GPT-SoVITS V2 weights without touching originals."""
import gc
import hashlib
import json
import logging
from pathlib import Path
import shutil
import time


SOURCE = Path('D:/appData/qqbot/小菲_GPT-SoVITS[1]')
RUNTIME = Path('D:/appData/qqbot/voise/runtime')
GSV_ROOT = RUNTIME.parent / 'gsv-lite'
PACKAGE_ID = 'xiaofei-v2'
REFERENCE_PREFIX = '被地板吃掉'
PROMPT_TEXT = '被地板吃掉。嗯就在室外喵。'
FILES = ('gpt/config.json', 'gpt/model.safetensors',
         'sovits/hps.json', 'sovits/model.safetensors', 'reference.wav')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    gpt_source = SOURCE / '小菲_GPT_weights_v2.ckpt'
    sovits_source = SOURCE / '小菲_SoVITS_weights_v2.pth'
    references = list((SOURCE / '小菲参考音频').glob(REFERENCE_PREFIX + '*.wav'))
    if len(references) != 1:
        raise SystemExit('Expected exactly one matching Xiaofei reference audio sample')
    reference_source = references[0]
    assets = GSV_ROOT / 'pretrained'
    required = [gpt_source, sovits_source, reference_source,
                assets / 'chinese-hubert-base', assets / 'g2p' / 'zh' / 'opencpop-strict.txt',
                assets / 'sv' / 'pretrained_eres2netv2w24s4ep4.ckpt']
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit('Required local model assets are missing: ' + ', '.join(missing))
    model_root = GSV_ROOT / 'models'
    final = model_root / PACKAGE_ID
    if final.exists():
        raise SystemExit('Refusing to overwrite the existing Xiaofei model package')
    if shutil.disk_usage(model_root).free < 1024**3:
        raise SystemExit('At least 1 GiB free disk space is required for conversion')

    import torch
    if not torch.cuda.is_available():
        raise SystemExit('CUDA is required for the accelerated conversion')
    free, total = torch.cuda.mem_get_info()
    if free < 2500 * 1024**2:
        raise SystemExit('Less than 2.5 GiB of GPU memory is free; conversion was not started')

    logging.disable(logging.INFO)
    from gsv_tts.TTS import TTS
    import gsv_tts.Config as gsv_config
    import gsv_tts.GPT_SoVITS.GPT.t2s_model as gsv_t2s_model
    from torch.nn.attention import SDPBackend
    gsv_config.SDPBACKEND = SDPBackend.EFFICIENT_ATTENTION
    gsv_t2s_model.SDPBACKEND = SDPBackend.EFFICIENT_ATTENTION

    stage = model_root / (PACKAGE_ID + '.staging-' + time.strftime('%Y%m%d-%H%M%S'))
    stage.mkdir()
    (stage / 'gpt').mkdir()
    (stage / 'sovits').mkdir()
    tts = TTS(models_dir=str(assets), device='cuda', dtype='float16', use_flash_attn=False,
              use_bert=False, gpt_cache=[(1, 512)], sovits_cache=[50])
    tts.to_safetensors(str(gpt_source), str(stage / 'gpt'))
    gc.collect()
    torch.cuda.empty_cache()
    tts.to_safetensors(str(sovits_source), str(stage / 'sovits'))
    gc.collect()
    torch.cuda.empty_cache()
    shutil.copy2(reference_source, stage / 'reference.wav')
    del tts
    gc.collect()
    torch.cuda.empty_cache()

    checksums = {relative: sha256(stage / relative) for relative in FILES}
    fingerprint_data = json.dumps({
        'model_family': 'gpt-sovits-v2',
        'runtime_version': '0.4.7',
        'checksums': checksums,
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    fingerprint = hashlib.sha256(fingerprint_data).hexdigest()[:24]
    manifest = {
        'format': 1,
        'voice_id': 'xiaofei',
        'voice_name': '小菲',
        'model_family': 'gpt-sovits-v2',
        'runtime': 'gsv-tts-lite',
        'runtime_version': '0.4.7',
        'files': list(FILES),
        'checksums': checksums,
        'fingerprint': fingerprint,
        'prompt_text': PROMPT_TEXT,
        'prompt_language': 'zh',
        'precision': 'float16',
        'source_weights_retained': True,
    }
    (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                                         encoding='utf-8')
    stage.rename(final)
    print(json.dumps({'package': str(final), 'fingerprint': fingerprint,
                      'gpu_free_before_bytes': free, 'gpu_total_bytes': total,
                      'files': {name: (final / name).stat().st_size for name in FILES}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
