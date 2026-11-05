"""Optional single-process FFmpeg extraction from bounded official local media."""
import asyncio
import json
import math
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from .bilibili_client import BiliError


def process_limits():
    os.umask(0o077)
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU,(22,24))
        resource.setrlimit(resource.RLIMIT_AS,(384*1024**2,384*1024**2))
        resource.setrlimit(resource.RLIMIT_FSIZE,(24*1024**2,24*1024**2))
        resource.setrlimit(resource.RLIMIT_NOFILE,(48,48))
    except (ImportError,ValueError):pass


async def run_process(args,timeout):
    process=await asyncio.create_subprocess_exec(*args,stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
        **({'preexec_fn':process_limits} if os.name=='posix' else {}))
    try:
        output,error=await asyncio.wait_for(process.communicate(),timeout)
        return process.returncode,output[:128*1024]
    except asyncio.TimeoutError:
        process.kill();await process.wait();return -1,b''
    finally:
        if process.returncode is None:process.kill();await process.wait()


def clear_workspace(root,work):
    # Only files created by this job. Unknown files/symlinks are never followed.
    if root.is_symlink() or work.is_symlink() or not re.fullmatch('[a-f0-9]{32}',work.name) or work.resolve().parent!=root.resolve():return
    for path in work.iterdir():
        if path.is_file() and not path.is_symlink() and (path.name=='media.mp4' or re.fullmatch(r'frame-\d{6}\.jpg',path.name)):
            if path.resolve().parent==work.resolve():path.unlink()
    if not any(work.iterdir()):work.rmdir()


async def extract_frames(path,start,end,*,ffmpeg=None,ffprobe=None):
    ffmpeg=ffmpeg or shutil.which('ffmpeg');ffprobe=ffprobe or shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:raise BiliError('media_unavailable','服务器没有可用的轻量视频处理器，使用快照模式')
    code,output=await run_process([ffprobe,'-v','error','-protocol_whitelist','file,pipe','-select_streams','v:0',
        '-show_entries','stream=width,height','-of','json',str(path)],6)
    if code:raise BiliError('media_unavailable','媒体格式未能确认，未继续解码')
    try:
        stream=json.loads(output)['streams'][0]
        if not 0<int(stream['width'])*int(stream['height'])<=1280*720:raise ValueError()
    except (ValueError,KeyError,IndexError,TypeError):raise BiliError('media_unavailable','媒体尺寸超出轻量解码范围') from None
    duration=max(.1,end-start);fps=min(2,120/duration)
    pattern=path.parent/'frame-%06d.jpg'
    code,_=await run_process([ffmpeg,'-hide_banner','-loglevel','error','-nostdin','-threads','1','-filter_threads','1',
        '-protocol_whitelist','file,pipe','-ss',str(start),'-i',str(path),'-t',str(duration),'-an',
        '-vf','fps='+str(fps)+',scale=320:-2:flags=area','-frames:v','120','-q:v','5','-start_number','0',str(pattern)],25)
    frames=[]
    for file in sorted(path.parent.glob('frame-*.jpg'))[:120]:
        if file.is_symlink() or not re.fullmatch(r'frame-\d{6}\.jpg',file.name) or file.stat().st_size>512*1024:continue
        stamp=start+int(file.stem.split('-')[1])/fps
        if stamp>=end:continue
        raw=file.read_bytes()
        if raw.startswith(b'\xff\xd8'):frames.append({'time':round(stamp,3),'jpeg':raw,'time_tolerance':round(1/fps,3)})
    if not frames:raise BiliError('media_unavailable','本次未取得有效解码帧，使用快照模式')
    return frames,{'media_mode':'ffmpeg_local','decoder_completed':code==0,'decoder_exit_code':code,'sample_fps':fps,
        'timestamp_precision':'resampled_seek_relative','timestamp_tolerance_seconds':round(1/fps,3),
        'decoded_sample_start':frames[0]['time'],'decoded_sample_end':frames[-1]['time'],
        'decoder_threads':1,'decoder_timeout_seconds':25}


async def media_frames(client,config,bvid,cid,start,end,budget):
    if not config.settings.bilibili_media_enabled or not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise BiliError('media_unavailable','未启用或缺少FFmpeg，使用官方快照降级')
    if os.name=='posix':
        available=next((int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')),0)
        if available<192*1024:raise BiliError('media_unavailable','空闲内存不足，保留快照模式，不启动视频解码')
    data=await client.api('/x/player/wbi/playurl',{'bvid':bvid,'cid':cid,'qn':16,'fnval':1,'fourk':0,'platform':'html5'},wbi=True,budget=budget)
    candidates=[row for row in (data.get('durl') or []) if isinstance(row,dict)]
    try:
        if not candidates:
            candidates=[row for row in (data.get('dash') or {}).get('video',[]) if isinstance(row,dict) and 0<int(row.get('height') or 0)<=480]
            candidates.sort(key=lambda row:int(row.get('bandwidth') or 0))
    except (TypeError,ValueError):raise BiliError('media_unavailable','低清媒体参数未确认，使用快照模式') from None
    if not candidates:raise BiliError('media_unavailable','没有取得普通账号可读取的低清媒体，不尝试解锁或更换提取器')
    row=candidates[0];url=row.get('url') or row.get('baseUrl') or row.get('base_url')
    if not isinstance(url,str) or not client.permitted(url,'media'):raise BiliError('media_unavailable','未提供获准的官方媒体地址，使用快照模式')
    if len(data.get('durl') or [])>1:raise BiliError('media_unavailable','分段媒体需要额外拼接，保留快照降级')
    cap=config.settings.bilibili_media_max_mb*1024**2
    try:oversize=bool(row.get('size') and int(row['size'])>cap)
    except (TypeError,ValueError):raise BiliError('media_unavailable','媒体体积未确认，使用快照模式') from None
    if oversize:raise BiliError('media_unavailable','媒体体积超过本次轻量处理范围')
    raw=await client.get(url,kind='media',budget=budget,cap=cap,json_body=False)
    root=config.directory/'bilibili-work'
    if root.is_symlink():raise BiliError('cache_path','视频工作目录异常')
    root.mkdir(mode=0o700,exist_ok=True);work=root/uuid.uuid4().hex;work.mkdir(mode=0o700)
    try:
        target=work/'media.mp4';fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(raw)
        return await extract_frames(target,start,end)
    finally:clear_workspace(root,work)
