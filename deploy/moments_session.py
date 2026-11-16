"""Durable ownership of a native Moments composer, never a second draft store."""
import hashlib
import json
import os
from pathlib import Path
import time


def digest(text):
    return hashlib.sha256(text.replace('\u2029','\n').replace('\r\n','\n').encode()).hexdigest()


def editor_snapshot(bounds):
    from a11y_safe import A11yBus, DESKTOP
    bus=A11yBus(deadline=time.monotonic()+5);seen=set();found=[]
    def visit(ref,inside=False):
        if ref in seen:return
        seen.add(ref);role=bus.role(ref);name=bus.properties(ref).get('Name') or ''
        if role in ('frame','window'):
            if name!='Moments':return
            inside=True
        if not inside and role in ('list','list-item'):return
        if inside and role=='text' and bus.bounds(ref)==bounds:
            value=bus.call(ref,'org.a11y.atspi.Text','GetText','(ii)',(0,-1))
            found.append({'ref':ref[0]+'|'+ref[1],'text_hash':digest(value),'chars':len(value)})
            return
        for child in bus.children(ref):
            try:visit(child,inside)
            except (TimeoutError,ValueError):raise
            except Exception:continue
    visit(DESKTOP)
    if len(found)!=1:raise ValueError('Cannot verify unique Moments composer text')
    return found[0]


class ComposerSession:
    def __init__(self,ui,account,job):
        self.ui,self.account,self.job=ui,account,Path(job).name
        self.path=ui.ROOT/'moments-editor-owner.json'
        self.owns=False;self.value={}

    def write(self):
        if self.path.is_symlink():raise ValueError('Invalid Moments ownership path')
        self.value['updated']=time.time()
        temporary=self.path.with_suffix('.pending')
        fd=os.open(temporary,os.O_CREAT|os.O_TRUNC|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'w') as stream:json.dump(self.value,stream)
        os.replace(temporary,self.path);self.path.chmod(0o600)

    def window(self):
        ids=self.ui.command('xdotool','search','--onlyvisible','--name','^Moments$').stdout.decode().split()
        if len(ids)!=1:raise ValueError('Cannot bind unique Moments composer window')
        return ids[0]

    def claim(self):
        f=self.ui.frame()
        if self.ui.editors(f):raise ValueError('Existing Moments composer is not owned by this job')
        self.value={'account':self.account,'job':self.job,'created':time.time(),'creator_pid':os.getpid(),
                    'window':self.window(),'wechat_pid':(f.get('window') or {}).get('pid'),
                    'stage':'preparing','editor':None,'expected_hash':digest('')}
        self.owns=True;self.write()

    def picker(self,window):
        if self.owns:self.value.update(picker_window=window);self.write()

    def checkpoint(self,expected):
        container=self.ui.composer(self.ui.tree());fields=self.ui.editors(container)
        if len(fields)!=1:raise ValueError('Cannot verify unique Moments composer')
        observed=editor_snapshot(fields[0]['bounds'])
        if observed['text_hash']!=digest(expected):raise ValueError('Moments composer text did not match the prepared text')
        self.value.update(stage='editing',editor=observed,expected_hash=digest(expected),window=self.window(),
                          wechat_pid=(self.ui.frame().get('window') or {}).get('pid'));self.write()

    def expect(self,text):
        if self.owns:self.value.update(expected_hash=digest(text));self.write()

    def can_close(self):
        if not self.owns:return False
        try:fields=self.ui.editors(self.ui.frame())
        except ValueError:return False
        if not fields:return True
        if len(fields)!=1:return False
        current=editor_snapshot(fields[0]['bounds']);old=self.value.get('editor') or {}
        return current['text_hash']==self.value.get('expected_hash',digest('')) and (
            not old.get('ref') or current['ref']==old['ref'])

    def submitting(self):
        if self.owns:self.value.update(stage='submitting');self.write()

    def closed(self):
        if self.owns:self.value.update(stage='closed');self.write()
        self.owns=False

    def recover(self):
        if not self.path.exists():return None
        try:old=json.loads(self.path.read_text())
        except (OSError,ValueError):return None
        if old.get('account')!=self.account or old.get('stage')=='closed':return None
        frames=[f for f in self.ui.walk(self.ui.tree()) if f.get('role')=='frame' and f.get('name')=='Moments']
        if len(frames)!=1:return None
        fields=self.ui.editors(frames[0])
        if not fields:return None
        if old.get('stage')=='submitting':return {'state':'blocked','code':'outcome_uncertain'}
        if not (0<=time.time()-float(old.get('updated',0))<=3600):return None
        if old.get('window')!=self.window() or old.get('wechat_pid')!=(frames[0].get('window') or {}).get('pid'):return None
        creator=old.get('creator_pid')
        if not isinstance(creator,int) or creator<=0:return None
        try:os.kill(creator,0)
        except ProcessLookupError:pass
        else:return {'state':'blocked','code':'editor_job_active'}
        if len(fields)!=1 or not isinstance(old.get('editor'),dict):return None
        current=editor_snapshot(fields[0]['bounds'])
        if current['ref']!=old['editor'].get('ref') or current['text_hash']!=old.get('expected_hash',old['editor'].get('text_hash')):return None
        # The original native process is gone, window/text identity is unchanged,
        # and no publish click was recorded. Cancel only this owned composer.
        self.value=old;self.owns=True
        self.ui.close_moments(owned_editor=True)
        self.closed()
        return {'state':'recovered','code':'owned_editor_closed'}
