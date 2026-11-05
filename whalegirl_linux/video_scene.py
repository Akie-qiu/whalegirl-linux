"""Small HSV scene sampler and timestamped storyboard, no local vision model.

HSV weighted circular difference is adapted from SAOS-3624/dsh-see-now cuts.js
and dsh-video-storyboard scan.js, Apache-2.0. License/NOTICE are vendored.
The bounded offline selector and Pillow layout are adapted for this server.
"""
import io
import math

from PIL import Image,ImageDraw,ImageOps


def signature(raw):
    with Image.open(io.BytesIO(raw)) as image:
        if image.width*image.height>16_000_000:raise ValueError('Frame too large')
        return image.convert('RGB').resize((64,36),Image.Resampling.BOX).convert('HSV').tobytes()


def hsv_difference(previous,current):
    if len(previous)!=len(current) or not current or len(current)%3:raise ValueError('Invalid HSV grid')
    total=0.0
    for index in range(0,len(current),3):
        hue=abs(current[index]-previous[index]);hue=min(hue,255-hue)
        weight=min(current[index+1],previous[index+1])/255
        total+=hue*weight+abs(current[index+1]-previous[index+1])+abs(current[index+2]-previous[index+2])
    return total/len(current)


def select_frames(candidates,limit=16,*,cut_threshold=18,motion_threshold=60):
    """Causal changes + one-frame flash rejection; never pad to a fixed count."""
    candidates=sorted((row for row in candidates if isinstance(row.get('time'),(int,float))
        and math.isfinite(row['time']) and row['time']>=0),key=lambda row:row['time'])[:120]
    unique=[]
    for row in candidates:
        if not unique or row['time']>unique[-1]['time']:unique.append(row)
    if not unique:return [],{'method':'hsv_scene_motion','candidates':0,'selected':0}
    grids=[signature(row['jpeg']) for row in unique]
    changes=[0]+[hsv_difference(grids[index-1],grids[index]) for index in range(1,len(grids))]
    chosen={0:('start',0.0)};anchor=0;motion=0.0;flashes=[]
    for index in range(1,len(unique)):
        gap=unique[index]['time']-unique[index-1]['time']
        score=changes[index]
        flash=(index+1<len(unique) and unique[index+1]['time']-unique[index-1]['time']<=1.5
            and score>=cut_threshold and changes[index+1]>=cut_threshold
            and hsv_difference(grids[index-1],grids[index+1])<cut_threshold*.35)
        if flash:flashes.append(index);continue
        if index-1 in flashes:continue
        novelty=hsv_difference(grids[anchor],grids[index])
        motion+=score*min(10,max(.05,gap))
        if score>=cut_threshold and novelty>=3:
            chosen[index]=('scene_cut',score);anchor=index;motion=0
        elif motion>=motion_threshold and novelty>=2:
            chosen[index]=('motion_integral',motion);anchor=index;motion=0
        elif unique[index]['time']-unique[anchor]['time']>=45 and novelty>=2:
            chosen[index]=('changed_hold',novelty);anchor=index;motion=0
    last=len(unique)-1
    if last not in flashes and hsv_difference(grids[anchor],grids[last])>=2:chosen[last]=('end',changes[last])
    limit=max(1,min(24,int(limit)))
    if len(chosen)>limit:
        indices=sorted(chosen);keep={indices[0],indices[-1]} if limit>1 else {indices[0]}
        # Allocate chronological bins only when real changes exceed the upper bound.
        lo,hi=unique[0]['time'],unique[-1]['time']
        for slot in range(max(0,limit-2)):
            a=lo+(hi-lo)*slot/max(1,limit-2);b=lo+(hi-lo)*(slot+1)/max(1,limit-2)
            options=[i for i in indices if i not in keep and a<=unique[i]['time']<=b]
            if options:keep.add(max(options,key=lambda i:chosen[i][1]))
        for index in sorted(indices,key=lambda i:-chosen[i][1]):
            if len(keep)>=limit:break
            keep.add(index)
        chosen={index:chosen[index] for index in sorted(keep)[:limit]}
    selected=[{**unique[index],'selection':chosen[index][0],'change_score':round(changes[index],3)} for index in sorted(chosen)]
    return selected,{'method':'hsv_scene_motion','grid':[64,36],'candidates':len(unique),'selected':len(selected),
        'cuts_detected':sum(reason[0]=='scene_cut' for reason in chosen.values()),'flash_frames_rejected':len(flashes),
        'scan_start':unique[0]['time'],'scan_end':unique[-1]['time'],
        'largest_candidate_gap':max((unique[i]['time']-unique[i-1]['time'] for i in range(1,len(unique))),default=0)}


def timestamp(value):
    value=max(0,float(value));hours=int(value//3600);minutes=int(value%3600//60);seconds=value%60
    return str(hours).zfill(2)+':'+str(minutes).zfill(2)+':'+format(seconds,'05.2f')


def storyboards(frames,per_sheet=12):
    result=[]
    for offset in range(0,len(frames),per_sheet):
        rows=frames[offset:offset+per_sheet];columns=min(3,len(rows));height=216;label=25;width=384
        canvas=Image.new('RGB',(columns*width,math.ceil(len(rows)/columns)*(height+label)),(22,24,28));draw=ImageDraw.Draw(canvas)
        cells=[]
        for index,row in enumerate(rows):
            x=index%columns*width;y=index//columns*(height+label)
            with Image.open(io.BytesIO(row['jpeg'])) as image:
                tile=ImageOps.contain(image.convert('RGB'),(width,height),Image.Resampling.LANCZOS)
                canvas.paste(tile,(x+(width-tile.width)//2,y+(height-tile.height)//2))
            number=offset+index+1;approx='~' if row.get('time_tolerance',0) else ''
            draw.text((x+7,y+height+5),str(number)+' | '+approx+timestamp(row['time']),fill=(242,244,247))
            cells.append({'number':number,'time':row['time'],'selection':row.get('selection',''),
                'time_tolerance':row.get('time_tolerance',0)})
        output=io.BytesIO();canvas.save(output,format='JPEG',quality=85)
        result.append({'jpeg':output.getvalue(),'cells':cells})
    return result
