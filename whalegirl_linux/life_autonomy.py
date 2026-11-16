"""Auditable, allowlisted changes to low-risk exploration preferences."""
import json
import re
import time
import uuid

from .settings import Settings


_CHAT_LIFE_INTENT_RE = re.compile(
    r'(?:生活参数|生活自治|自主调整|探索(?:开放度|间隔|频率|节奏)?|'
    r'(?:b站|bilibili|贴吧|朋友圈).{0,8}(?:间隔|频率|节奏|多刷|少刷|多看|少看)|'
    r'(?:间隔|频率|节奏|开放度).{0,6}(?:调|改|加|减|增|降|放慢|加快|快点|慢点|高点|低点)|'
    r'想看的类型|换换口味|换点题材|喜欢看什么|调整.{0,6}参数|回滚.{0,6}参数|'
    r'life_(?:inspect|preview|apply|observe|rollback))',
    re.I,
)


PARAMETERS = {
    'growth_exploration': {
        'label': '探索开放度', 'minimum': 0, 'maximum': 100, 'unit': '/100',
        'effect': '低值更偏向已形成的兴趣，高值更愿意尝试陌生题材。',
    },
    'growth_interval_minutes': {
        'label': '成长探索间隔', 'minimum': 30, 'maximum': 1440, 'unit': '分钟',
        'effect': '影响成长调度中的记忆整理与贴吧学习间隔。',
    },
    'bilibili_interval_minutes': {
        'label': 'B站自主探索间隔', 'minimum': 30, 'maximum': 1440, 'unit': '分钟',
        'effect': '影响下一次 B 站自主探索排期。',
    },
    'tieba_interval_minutes': {
        'label': '贴吧浏览间隔', 'minimum': 10, 'maximum': 1440, 'unit': '分钟',
        'effect': '影响下一次贴吧浏览排期。',
    },
}


class LifeAutonomyService:
    """Transactions over a deliberately small settings whitelist.

    All durable policy and history lives in SQLite; actual settings remain in
    Config and are written only through Config.save().
    """

    def __init__(self, runtime):
        self.runtime = runtime
        self.config = runtime.config
        self.store = runtime.store
        self.db = self.store.db
        with self.store.lock, self.db:
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS life_parameter_changes(
                    id TEXT PRIMARY KEY, account TEXT NOT NULL, role_id TEXT NOT NULL,
                    parameter TEXT NOT NULL, previous_value INTEGER NOT NULL,
                    target_value INTEGER NOT NULL, reason TEXT NOT NULL,
                    initiator TEXT NOT NULL DEFAULT 'admin',
                    state TEXT NOT NULL, preview_json TEXT NOT NULL DEFAULT '{}',
                    applied_generation INTEGER NOT NULL DEFAULT 0,
                    applied_at REAL NOT NULL DEFAULT 0,
                    created REAL NOT NULL, updated REAL NOT NULL,
                    error TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS life_parameter_scope
                    ON life_parameter_changes(account,role_id,created);
                CREATE TABLE IF NOT EXISTS life_parameter_events(
                    id INTEGER PRIMARY KEY, change_id TEXT NOT NULL,
                    stage TEXT NOT NULL, details TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS life_parameter_events_change
                    ON life_parameter_events(change_id,id);
                CREATE TABLE IF NOT EXISTS life_parameter_heads(
                    account TEXT NOT NULL,role_id TEXT NOT NULL,parameter TEXT NOT NULL,
                    change_id TEXT NOT NULL,revision INTEGER NOT NULL,value INTEGER NOT NULL,
                    updated REAL NOT NULL,PRIMARY KEY(account,role_id,parameter));
            ''')
            columns={row[1] for row in self.db.execute('PRAGMA table_info(life_parameter_changes)')}
            if 'initiator' not in columns:
                self.db.execute("ALTER TABLE life_parameter_changes ADD COLUMN initiator TEXT NOT NULL DEFAULT 'admin'")
            if 'applied_at' not in columns:
                self.db.execute('ALTER TABLE life_parameter_changes ADD COLUMN applied_at REAL NOT NULL DEFAULT 0')

    @staticmethod
    def _value(parameter, value):
        spec = PARAMETERS.get(parameter)
        if not spec:
            raise ValueError('这个生活参数不在可调整目录中')
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError('生活参数必须是整数')
        if not spec['minimum'] <= value <= spec['maximum']:
            raise ValueError(f"{spec['label']}范围为{spec['minimum']}～{spec['maximum']}{spec['unit']}")
        return value

    def _role(self):
        return self.runtime.memory_hub.role()

    def _scope(self):
        return self.config.settings.account_id, self._role()

    def _assert_actor(self, actor):
        if actor not in ('admin','ai'):
            raise ValueError('生活参数操作者无效')
        if actor=='ai' and not getattr(self.config.settings,'life_autonomy_enabled',False):
            raise ValueError('生活自治已关闭，AI不能读取或修改生活参数')

    def _head(self, parameter):
        raw=self.db.execute('SELECT * FROM life_parameter_heads WHERE account=? AND role_id=? AND parameter=?',
            (*self._scope(),parameter)).fetchone()
        if raw:return dict(raw)
        # Older databases have no head row. Preserve the latest applied change
        # as their starting version instead of making old transactions current.
        raw=self.db.execute('''SELECT id,target_value,previous_value,state,updated FROM life_parameter_changes
            WHERE account=? AND role_id=? AND parameter=? AND (applied_at>0
                OR state IN ('applied','adopted','observed','rolled_back','rollback_conflict'))
            ORDER BY CASE WHEN applied_at>0 THEN applied_at ELSE updated END DESC,created DESC LIMIT 1''',
            (*self._scope(),parameter)).fetchone()
        if not raw:return {'change_id':'','revision':0,'value':getattr(self.config.settings,parameter)}
        return {'change_id':raw['id'],'revision':1,
            'value':raw['previous_value'] if raw['state']=='rolled_back' else raw['target_value']}

    def _write_head(self, parameter, identity, revision, value):
        self.db.execute('''INSERT INTO life_parameter_heads VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(account,role_id,parameter) DO UPDATE SET change_id=excluded.change_id,
            revision=excluded.revision,value=excluded.value,updated=excluded.updated''',
            (*self._scope(),parameter,identity,revision,value,time.time()))

    def _event(self, identity, stage, details):
        with self.store.lock, self.db:
            self._insert_event(identity,stage,details)

    def _insert_event(self, identity, stage, details):
        self.db.execute('INSERT INTO life_parameter_events(change_id,stage,details,created) VALUES(?,?,?,?)',
            (identity, stage, json.dumps(details, ensure_ascii=False), time.time()))

    def _scheduler_state(self, parameter):
        account, role = self._scope()
        if parameter in ('growth_interval_minutes', 'growth_exploration'):
            row = self.db.execute('SELECT memory_at,world_at FROM growth_state WHERE account=? AND role_id=?',
                (account, role)).fetchone()
            return dict(row) if row else {'memory_at': 0, 'world_at': 0}
        if parameter == 'bilibili_interval_minutes':
            row = self.db.execute('SELECT next_at FROM bilibili_schedule WHERE account=? AND role=?',
                (account, role)).fetchone()
            return {'next_at': float(row['next_at']) if row else 0}
        row = self.db.execute('SELECT next_at FROM tieba_state WHERE account=?', (account,)).fetchone()
        return {'next_at': float(row['next_at']) if row else 0}

    def _scheduler_baseline(self, parameter):
        # Initializing through the existing scheduler preserves its real first
        # deadline. A missing row must not become a zero deadline on rollback.
        if parameter == 'growth_interval_minutes':
            self.runtime.growth.schedule()
        elif parameter == 'bilibili_interval_minutes':
            self.runtime.bilibili.schedule()
        return self._scheduler_state(parameter)

    def catalog(self):
        settings = self.config.settings
        return {'enabled': bool(getattr(settings, 'life_autonomy_enabled', True)),
            'parameters': [{**spec, 'key': key, 'value': getattr(settings, key)} for key, spec in PARAMETERS.items()],
            'immutable': ['账号与登录凭据', '微信发送与会话授权', 'B站/贴吧账号写操作权限',
                '核心人设与安全规则', '模型凭据、累计费用策略、代码和部署权限'],
            'transactions': self.history(20)}

    def inspect(self):
        settings = self.config.settings
        values = {}
        for key, spec in PARAMETERS.items():
            values[key] = {'value': getattr(settings, key), 'label': spec['label'],
                'effect': spec['effect'], 'range': [spec['minimum'], spec['maximum']],
                'scheduler': self._scheduler_state(key)}
        with self.store.lock:
            transactions=[dict(row) for row in self.db.execute('''SELECT id,parameter,previous_value,
                target_value,initiator,reason,state,applied_at FROM life_parameter_changes
                WHERE account=? AND role_id=? ORDER BY created DESC LIMIT 8''',self._scope())
                if row['parameter'] in PARAMETERS]
        return {'enabled':bool(getattr(settings,'life_autonomy_enabled',False)),
            'generation': self.config.generation, 'parameters': values,'transactions':transactions}

    def preview(self, parameter, value, reason='', *, actor='admin'):
        self._assert_actor(actor)
        target = self._value(parameter, value)
        current = getattr(self.config.settings, parameter)
        identity = uuid.uuid4().hex
        spec = PARAMETERS[parameter]
        effect = {'parameter': parameter, 'label': spec['label'], 'from': current, 'to': target,
            'unit': spec['unit'], 'description': spec['effect'],
            'scheduler_before': self._scheduler_state(parameter),
            'scheduler_after': self._preview_schedule(parameter, target),
            'risk': '低：只改探索偏好或间隔，不改权限、账号、费用边界与核心人格',
            'requires_admin': False}
        now = time.time()
        with self.config.lock,self.store.lock, self.db:
            self._assert_actor(actor)
            # Re-read under both locks so a preview cannot mix a value from an
            # old setting with the version of a newer transaction.
            current=getattr(self.config.settings,parameter)
            effect['from']=current
            effect['scheduler_before']=self._scheduler_state(parameter)
            effect['scheduler_after']=self._preview_schedule(parameter,target)
            effect['head_revision']=self._head(parameter)['revision']
            effect['config_generation']=self.config.generation
            self.db.execute('''INSERT INTO life_parameter_changes
                (id,account,role_id,parameter,previous_value,target_value,reason,initiator,state,preview_json,created,updated)
                VALUES(?,?,?,?,?,?,?,?,'previewed',?,?,?)''',
                (identity, *self._scope(), parameter, current, target, str(reason or '')[:240], actor,
                 json.dumps(effect, ensure_ascii=False), now, now))
        self._event(identity, 'previewed', effect)
        return {'id': identity, 'state': 'previewed', **effect}

    def _preview_schedule(self, parameter, target):
        now = time.time()
        if parameter == 'growth_interval_minutes':
            deadline = now + target * 60
            return {'memory_at': deadline, 'world_at': deadline}
        if parameter == 'bilibili_interval_minutes':
            return {'next_at': now + target * 60}
        if parameter == 'tieba_interval_minutes':
            return {'next_at': now + target * 60}
        return self._scheduler_state(parameter)

    def apply(self, identity, *, actor='admin'):
        self._assert_actor(actor)
        # Serialize compare-and-save across console and model actions. Otherwise
        # two previews based on the same old value could both overwrite each other.
        with self.config.lock,self.store.lock:
            self._assert_actor(actor)
            raw = self.db.execute('SELECT * FROM life_parameter_changes WHERE id=? AND account=? AND role_id=?',
                (identity, *self._scope())).fetchone()
            if not raw:
                raise ValueError('找不到当前角色的生活参数预览')
            row = dict(raw)
            if actor=='ai' and row['initiator']!='ai':
                raise ValueError('AI只能应用自己发起的参数预览')
            if row['state'] not in ('previewed', 'proposed'):
                raise ValueError('这项预览已经处理，不能重复应用')
            parameter = row['parameter']; target = self._value(parameter, row['target_value'])
            current = getattr(self.config.settings, parameter)
            head=self._head(parameter)
            preview=json.loads(row['preview_json'] or '{}')
            if (current != row['previous_value']
                    or preview.get('head_revision',head['revision'])!=head['revision']
                    or preview.get('config_generation',self.config.generation)!=self.config.generation):
                with self.db:
                    self.db.execute("UPDATE life_parameter_changes SET state='conflict',error=?,updated=? WHERE id=?",
                        ('配置已被其他操作修改；为避免覆盖，未应用', time.time(), identity))
                self._event(identity, 'conflict', {'expected': row['previous_value'], 'actual': current})
                raise ValueError('配置已变化，安全预览失效；请重新读取并预览')
            values = self.config.settings.model_dump()
            values[parameter] = target
            original=self.config.settings
            scheduler_before=self._scheduler_baseline(parameter)
            applied_at=time.time()
            try:
                self.config.save(Settings.model_validate(values))
                scheduler = self._adopt_scheduler(parameter, target, now=applied_at)
                if parameter!='growth_exploration' and not scheduler.get('adopted'):
                    raise ValueError('调度器未采用新排期')
                generation = self.config.generation
                actual = getattr(self.config.settings, parameter)
                if actual != target:
                    raise ValueError('运行中配置读回与目标不符')
                adopted = bool(scheduler.get('adopted'))
                state = 'adopted' if adopted else 'applied'
                with self.db:
                    self._write_head(parameter,identity,head['revision']+1,target)
                    preview['scheduler_before_apply']=scheduler_before
                    preview['applied_head_revision']=head['revision']+1
                    self.db.execute('''UPDATE life_parameter_changes
                        SET state=?,applied_generation=?,applied_at=?,updated=?,error='',preview_json=? WHERE id=?''',
                        (state,generation,applied_at,time.time(),json.dumps(preview,ensure_ascii=False),identity))
                    self._insert_event(identity,'applied',{'value':actual,'generation':generation,'initiator':row['initiator']})
                    if adopted:self._insert_event(identity,'adopted',scheduler)
            except Exception as exc:
                restored=self._compensate(parameter,original,scheduler_before)
                with self.db:
                    self.db.execute("UPDATE life_parameter_changes SET state='failed',error=?,updated=? WHERE id=?",
                        (type(exc).__name__+('；已恢复原设置' if restored else '；恢复失败，需要管理员核对'), time.time(), identity))
                self._event(identity, 'failed', {'error': type(exc).__name__,'restored':restored})
                raise ValueError('生活参数应用失败，'+('已恢复原设置和排期' if restored else '恢复未完成，请管理员核对配置与排期')) from exc
        return {'id': identity, 'state': state, 'parameter': parameter, 'value': actual,
            'generation': generation, 'scheduler': scheduler,
            'meaning': '已写入配置并由运行中服务读回' + ('，实际调度时间已更新' if adopted else '；调度器采用证据待补齐')}

    def _adopt_scheduler(self, parameter, value, *, now=None):
        account, role = self._scope();now = time.time() if now is None else now;deadline = now + value * 60
        try:
            with self.store.lock, self.db:
                if parameter == 'growth_interval_minutes':
                    self.db.execute('''INSERT INTO growth_state(account,role_id,memory_at,world_at) VALUES(?,?,?,?)
                        ON CONFLICT(account,role_id) DO UPDATE SET memory_at=excluded.memory_at,world_at=excluded.world_at''',
                        (account, role, deadline, deadline))
                    readback = self.db.execute('SELECT memory_at,world_at FROM growth_state WHERE account=? AND role_id=?',
                        (account, role)).fetchone()
                    adopted = bool(readback and abs(readback['memory_at']-deadline)<1 and abs(readback['world_at']-deadline)<1)
                    return {'adopted': adopted, 'deadlines': dict(readback) if readback else {}}
                if parameter == 'bilibili_interval_minutes':
                    self.db.execute('''INSERT INTO bilibili_schedule(account,role,next_at,curiosity) VALUES(?,?,?,'')
                        ON CONFLICT(account,role) DO UPDATE SET next_at=excluded.next_at''', (account, role, deadline))
                    readback = self.db.execute('SELECT next_at FROM bilibili_schedule WHERE account=? AND role=?',
                        (account, role)).fetchone()
                    adopted = bool(readback and abs(readback['next_at']-deadline)<1)
                    return {'adopted': adopted, 'deadlines': {'next_at': float(readback['next_at'])} if readback else {}}
                if parameter == 'tieba_interval_minutes':
                    self.db.execute('''INSERT INTO tieba_state(account,next_at) VALUES(?,?)
                        ON CONFLICT(account) DO UPDATE SET next_at=excluded.next_at''', (account, deadline))
                    readback = self.db.execute('SELECT next_at FROM tieba_state WHERE account=?', (account,)).fetchone()
                    adopted = bool(readback and abs(readback['next_at']-deadline)<1)
                    return {'adopted': adopted, 'deadlines': {'next_at': float(readback['next_at'])} if readback else {}}
        except Exception as exc:
            raise ValueError('调度排期写入或读回失败') from exc
        # Config readback proves the write, but actual adoption needs a real
        # selection event that records this value.
        return {'adopted': False, 'config_readback': getattr(self.config.settings, parameter) == value,
            'adoption_kind': 'waiting_for_real_selection'}

    def _restore_scheduler(self, parameter, before):
        """Restore the exact previous deadline, preserving an existing schedule."""
        if parameter == 'growth_exploration':
            return True  # Openness does not own or change a scheduling deadline.
        account,role=self._scope()
        with self.store.lock,self.db:
            if parameter=='growth_interval_minutes':
                self.db.execute('''INSERT INTO growth_state(account,role_id,memory_at,world_at) VALUES(?,?,?,?)
                    ON CONFLICT(account,role_id) DO UPDATE SET memory_at=excluded.memory_at,world_at=excluded.world_at''',
                    (account,role,float(before.get('memory_at') or 0),float(before.get('world_at') or 0)))
            elif parameter=='bilibili_interval_minutes':
                self.db.execute('''INSERT INTO bilibili_schedule(account,role,next_at,curiosity) VALUES(?,?,?,'')
                    ON CONFLICT(account,role) DO UPDATE SET next_at=excluded.next_at''',
                    (account,role,float(before.get('next_at') or 0)))
            elif parameter=='tieba_interval_minutes':
                self.db.execute('''INSERT INTO tieba_state(account,next_at) VALUES(?,?)
                    ON CONFLICT(account) DO UPDATE SET next_at=excluded.next_at''',
                    (account,float(before.get('next_at') or 0)))
        return self._scheduler_state(parameter)==before

    def _compensate(self, parameter, original, scheduler_before):
        configured=False;scheduled=False
        try:
            self.config.save(original)
            configured=getattr(self.config.settings,parameter)==getattr(original,parameter)
        except Exception:
            pass
        try:scheduled=self._restore_scheduler(parameter,scheduler_before)
        except Exception:pass
        return configured and scheduled

    def propose_and_apply(self, parameter, value, reason='', *, actor='ai'):
        preview = self.preview(parameter, value, reason, actor=actor)
        result = self.apply(preview['id'], actor=actor)
        return {**preview, **result}

    def rollback(self, identity, reason='', *, actor='admin'):
        self._assert_actor(actor)
        with self.config.lock,self.store.lock:
            self._assert_actor(actor)
            raw=self.db.execute('SELECT * FROM life_parameter_changes WHERE id=? AND account=? AND role_id=?',
                (identity,*self._scope())).fetchone()
            if not raw:raise ValueError('找不到当前角色的参数变更')
            row=dict(raw)
            if actor=='ai' and row['initiator']!='ai':
                raise ValueError('AI只能回滚自己发起的参数变更')
            if row['state'] not in ('applied','adopted','observed'):
                raise ValueError('只有已应用的参数可以回滚')
            parameter=row['parameter'];current=getattr(self.config.settings,parameter)
            preview=json.loads(row['preview_json'] or '{}');head=self._head(parameter)
            if (current!=row['target_value'] or head['change_id']!=identity
                    or preview.get('applied_head_revision',head['revision'])!=head['revision']):
                with self.db:
                    self.db.execute("UPDATE life_parameter_changes SET state='rollback_conflict',error=?,updated=? WHERE id=?",
                        ('当前值已改变，未覆盖后续设置',time.time(),identity))
                self._event(identity,'rollback_conflict',{'expected':row['target_value'],'actual':current})
                raise ValueError('参数已被其他操作改变；为避免覆盖，不执行回滚')
            original=self.config.settings;scheduler_current=self._scheduler_baseline(parameter)
            scheduler_before=preview.get('scheduler_before_apply')
            legacy=not isinstance(scheduler_before,dict)
            if legacy:scheduler_before=self._preview_schedule(parameter,row['previous_value'])
            values=original.model_dump();values[parameter]=row['previous_value']
            try:
                self.config.save(Settings.model_validate(values))
                if not self._restore_scheduler(parameter,scheduler_before):
                    raise ValueError('原排期恢复后读回不一致')
                if getattr(self.config.settings,parameter)!=row['previous_value']:
                    raise ValueError('原配置恢复后读回不一致')
                scheduler={'restored':True,'deadlines':self._scheduler_state(parameter),
                    'restore_kind':'legacy_recomputed_deadline' if legacy else 'saved_previous_deadline'}
                with self.db:
                    self._write_head(parameter,identity,head['revision']+1,row['previous_value'])
                    self.db.execute("UPDATE life_parameter_changes SET state='rolled_back',updated=?,error='' WHERE id=?",
                        (time.time(),identity))
                    self._insert_event(identity,'rolled_back',{'value':row['previous_value'],'scheduler':scheduler,
                        'reason':str(reason or '')[:240]})
            except Exception as exc:
                restored=self._compensate(parameter,original,scheduler_current)
                self._event(identity,'rollback_failed',{'error':type(exc).__name__,'restored':restored})
                raise ValueError('生活参数回滚失败，'+('已恢复回滚前设置和排期' if restored else '恢复未完成，请管理员核对配置与排期')) from exc
        return {'id': identity, 'state': 'rolled_back', 'parameter': parameter,
            'value': getattr(self.config.settings, parameter), 'scheduler': scheduler}

    def observe(self, identity):
        with self.config.lock,self.store.lock:
            raw=self.db.execute('SELECT * FROM life_parameter_changes WHERE id=? AND account=? AND role_id=?',
                (identity,*self._scope())).fetchone()
            if not raw:raise ValueError('找不到当前角色的参数变更')
            row=dict(raw);actual=getattr(self.config.settings,row['parameter'])
            scheduler=self._scheduler_state(row['parameter']);configured=actual==row['target_value']
            behavior=self._behavior_after(row)
            head=self._head(row['parameter']);preview=json.loads(row['preview_json'] or '{}')
            eligible=(row['state'] in ('applied','adopted','observed') and head['change_id']==identity
                and preview.get('applied_head_revision',head['revision'])==head['revision'])
            result={'id':identity,'configured_value':actual,'target_value':row['target_value'],
                'configured':configured,'active':eligible,'scheduler':scheduler,'behavior':behavior,
                'state':'observed' if eligible and configured and behavior['count']>0 else row['state']}
            if result['state']=='observed' and row['state']!='observed':
                with self.db:self.db.execute("UPDATE life_parameter_changes SET state='observed',updated=? WHERE id=?",
                    (time.time(),identity))
                self._event(identity,'observed',result)
            return result

    def record_behavior(self, parameter, value, evidence):
        """Attach a real runtime behavior to matching live setting transactions."""
        if parameter not in PARAMETERS or isinstance(value,bool) or not isinstance(value,int):return 0
        if not isinstance(evidence,dict) or not evidence:return 0
        account,role=self._scope();now=time.time();recorded=0
        if parameter!='growth_exploration' and evidence.get('origin')!='scheduler':return 0
        details={**evidence,'parameter':parameter,'value':value}
        encoded=json.dumps(details,ensure_ascii=False,sort_keys=True)
        with self.config.lock,self.store.lock,self.db:
            if getattr(self.config.settings,parameter,None)!=value:return 0
            head=self._head(parameter)
            row=self.db.execute('''SELECT id,applied_at,state,target_value FROM life_parameter_changes
                WHERE account=? AND role_id=? AND parameter=? AND id=?''',(account,role,parameter,head['change_id'])).fetchone()
            if row and row['target_value']==value and row['state'] in ('applied','adopted','observed') and row['applied_at']>0:
                if not self._verified_runtime_event(parameter,row,evidence):return 0
                exists=self.db.execute("SELECT 1 FROM life_parameter_events WHERE change_id=? AND stage='behavior_observed' AND details=?",
                    (row['id'],encoded)).fetchone()
                if not exists:
                    self.db.execute('INSERT INTO life_parameter_events(change_id,stage,details,created) VALUES(?,?,?,?)',
                        (row['id'],'behavior_observed',encoded,now))
                    if row['state']!='observed':
                        self.db.execute("UPDATE life_parameter_changes SET state='observed',updated=? WHERE id=?",(now,row['id']))
                        self.db.execute('INSERT INTO life_parameter_events(change_id,stage,details,created) VALUES(?,?,?,?)',
                            (row['id'],'observed',json.dumps({'evidence':details},ensure_ascii=False),now))
                    recorded=1
        return recorded

    def _verified_runtime_event(self, parameter, change, evidence):
        """Verify behavior against durable runtime records, not caller fields alone."""
        run_id=str(evidence.get('run_id') or '')
        if not run_id:return False
        if parameter=='growth_exploration':
            if evidence.get('origin')!='selection' or evidence.get('source') not in ('bilibili','tieba'):return False
            run=self.db.execute('SELECT account,role_id,kind,state,started_at FROM growth_runs WHERE id=?',(run_id,)).fetchone()
            if (not run or run['account']!=self.config.settings.account_id or run['role_id']!=self._role()
                    or run['kind']!=evidence['source']
                    or run['state'] not in ('planning','reading','reflecting','done')
                    or float(run['started_at'] or 0)<float(change['applied_at'])):return False
            selected_key='bvid' if evidence['source']=='bilibili' else 'tid'
            selected_value=str(evidence.get(selected_key) or '')
            rows=self.db.execute("SELECT details FROM growth_events WHERE run_id=? AND stage='candidate_selected' AND created>=? ORDER BY id DESC",
                (run_id,float(change['applied_at']))).fetchall()
            for raw in rows:
                try:details=json.loads(raw['details'] or '{}')
                except (TypeError,ValueError):continue
                if (str(details.get(selected_key) or '')==selected_value and selected_value
                        and details.get('exploration_openness')==change['target_value']):return True
            return False
        if evidence.get('origin')!='scheduler':return False
        try:due=float(evidence.get('scheduled_due_at'))
        except (TypeError,ValueError):return False
        if due < float(change['applied_at'])+int(change['target_value'])*60:return False
        if parameter=='tieba_interval_minutes':
            run=self.db.execute('SELECT started,finished,state,read_count,origin,scheduled_due_at FROM tieba_runs WHERE id=? AND account=?',
                (run_id,self.config.settings.account_id)).fetchone()
            return bool(run and run['state']=='done' and run['read_count']>0
                and run['origin']=='scheduler' and float(run['scheduled_due_at'] or 0)==due
                and float(run['started'])>=due and float(run['finished'] or 0)>=float(run['started']))
        run=self.db.execute('SELECT * FROM growth_runs WHERE id=? AND account=? AND role_id=?',
            (run_id,*self._scope())).fetchone()
        if not run or run['state']!='done' or float(run['started_at'] or 0)<due:return False
        try:result=json.loads(run['result'] or '{}');request=result.get('request',{})
        except (TypeError,ValueError):return False
        if (request.get('origin')!='scheduler' or float(request.get('scheduled_due_at') or 0)<due
                or float(request.get('scheduled_due_at') or 0)!=due):return False
        if parameter=='bilibili_interval_minutes':
            return run['kind']=='bilibili' and int(result.get('readings') or 0)>0
        return run['kind'] in ('memory','tieba')

    def _behavior_after(self, row):
        applied_at=float(row.get('applied_at') or 0)
        # Count in SQLite and deserialize only the small evidence window. A
        # long-lived parameter can accumulate months of observations.
        where="""change_id=? AND stage='behavior_observed' AND created>=?
            AND json_extract(CASE WHEN json_valid(details) THEN details ELSE '{}' END,'$.parameter')=?
            AND json_extract(CASE WHEN json_valid(details) THEN details ELSE '{}' END,'$.value')=?"""
        args=(row['id'],applied_at,row['parameter'],row['target_value'])
        count=self.db.execute('SELECT count(*) FROM life_parameter_events WHERE '+where,args).fetchone()[0]
        events=self.db.execute('SELECT details FROM life_parameter_events WHERE '+where+' ORDER BY id DESC LIMIT 5',args).fetchall()
        evidence=[json.loads(event['details']) for event in reversed(events)]
        return {'count':count,'basis':'与此参数值匹配的真实运行事件','verified':count>0,'evidence':evidence}

    def history(self, limit=40):
        account, role = self._scope();limit = min(100, max(1, int(limit)))
        result = []
        with self.store.lock:
            for raw in self.db.execute('SELECT * FROM life_parameter_changes WHERE account=? AND role_id=? ORDER BY created DESC LIMIT ?',
                    (account, role, limit)):
                row = dict(raw)
                row['preview'] = json.loads(row.pop('preview_json') or '{}')
                page = self.event_page(row['id'])
                row.update(events=page['items'],event_total=page['event_total'],
                    events_truncated=page['events_truncated'],events_next=page['next'])
                result.append(row)
        return result

    def event_page(self, identity, before=0, limit=40):
        """Read a bounded event window without pruning the durable audit trail."""
        before=max(0,int(before));limit=min(40,max(1,int(limit)))
        with self.store.lock:
            scoped=self.db.execute('SELECT id FROM life_parameter_changes WHERE id=? AND account=? AND role_id=?',
                (identity,*self._scope())).fetchone()
            if not scoped:raise ValueError('找不到当前角色的参数变更')
            total=self.db.execute('SELECT count(*) FROM life_parameter_events WHERE change_id=?',
                (identity,)).fetchone()[0]
            cursor=' AND id<?' if before else ''
            args=(identity,before,limit+1) if before else (identity,limit+1)
            rows=self.db.execute('SELECT * FROM life_parameter_events WHERE change_id=?'+cursor+' ORDER BY id DESC LIMIT ?',
                args).fetchall()
            more=len(rows)>limit;rows=rows[:limit]
            items=[{**dict(row),'details':json.loads(row['details'] or '{}')} for row in reversed(rows)]
            return {'change_id':identity,'items':items,'event_total':total,
                'events_truncated':total>len(items),'has_more':more,
                'next':items[0]['id'] if more and items else None}

    def model_prompt(self):
        if not getattr(self.config.settings, 'life_autonomy_enabled', True):
            return ''
        catalog = [{**spec, 'key': key, 'value': getattr(self.config.settings, key)} for key, spec in PARAMETERS.items()]
        concise=[{key:item[key] for key in ('key','value','minimum','maximum','unit')} for item in catalog]
        return ('\n【可自主调整的生活参数】你可以按兴趣和实际探索经验决定是否调整探索开放度与间隔，不必为了调用而调用。'
            '仅限这些白名单参数：'+json.dumps(concise,ensure_ascii=False)+'。'
            '你可读取状态、预览修改或直接提交低风险试行，系统会在应用前保存旧值与预览；也可观察实际采用、冲突时安全回滚。'
            '必须基于经历说明理由。工具结果回来后再陈述，不能把提议或预览说成已生效。'
            '没有工具能力的接口使用 JSON 的 life_request 字段，且每轮至多一个操作。')

    @staticmethod
    def should_offer_tools(first, batch=(), settings=None):
        """Gate chat-entry life_* tools/prompt away from pat and ordinary casual chat."""
        if not first or first.get('trigger') in ('pat', 'completion_notice'):
            return False
        texts = []
        for item in (batch or (first,)):
            if int(item.get('kind') or 1) != 1:
                continue
            raw = str(item.get('content') or '').strip()
            if not raw or re.fullmatch(r'\[(?:表情包|动画表情|原生表情|原生表情包)[^\]]*\]', raw):
                continue
            texts.append(raw)
        if not texts:
            return False
        joined = ' '.join(texts)
        return bool(_CHAT_LIFE_INTENT_RE.search(joined))

    @staticmethod
    def tools(engine, settings):
        if (not getattr(settings,'life_autonomy_enabled',False)
                or getattr(engine,'api_style','')!='openai'
                or getattr(engine,'stream_enabled',False)
                or not hasattr(engine,'chat_with_tools')):return []
        schemas={
            'life_inspect':('读取允许调整的生活参数、当前设置和近期变更。不得修改任何东西。',{}),
            'life_preview':('预览一个白名单生活参数的低风险调整，不应用设置。',
                {'parameter':{'type':'string','enum':list(PARAMETERS)},'value':{'type':'integer'},'reason':{'type':'string','maxLength':180}}),
            'life_apply':('把一个值得试行的白名单参数先保存预览再应用。只能改生活参数目录中的四项，不能改账号、权限、凭据、费用边界、核心人格、代码或部署。',
                {'parameter':{'type':'string','enum':list(PARAMETERS)},'value':{'type':'integer'},'reason':{'type':'string','maxLength':180}}),
            'life_observe':('核对一次变更的配置值、调度采用证据和之后真实行为。',
                {'change_id':{'type':'string','minLength':8,'maxLength':64}}),
            'life_rollback':('安全回滚一项由你发起的已应用参数；如果值被后续操作改过，工具会拒绝覆盖。',
                {'change_id':{'type':'string','minLength':8,'maxLength':64},'reason':{'type':'string','maxLength':180}}),
        }
        return [{'type':'function','function':{'name':name,'description':description,
            'parameters':{'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}}}
            for name,(description,properties) in schemas.items()]

    @staticmethod
    def native_decision(result):
        mapping={'life_inspect':'inspect','life_preview':'preview','life_apply':'apply',
            'life_observe':'observe','life_rollback':'rollback'}
        calls=[call for call in result.get('tool_calls') or [] if isinstance(call,dict)
            and (call.get('function') or {}).get('name') in mapping]
        if not calls:return None
        if len(calls)!=1:return {'should_reply':True,'actions':[],'life_request':{'action':'invalid'}}
        call=calls[0];function=call.get('function') or {}
        try:arguments=json.loads(function.get('arguments') or '{}')
        except (TypeError,ValueError):return {'should_reply':True,'actions':[],'life_request':{'action':'invalid'}}
        if not isinstance(arguments,dict):arguments={}
        return {'should_reply':True,'actions':[],'life_request':{**arguments,'action':mapping[function['name']]}}

    def execute_request(self, request):
        self._assert_actor('ai')
        if not isinstance(request, dict):
            raise ValueError('生活参数请求格式无效')
        action = request.get('action')
        if action == 'inspect':
            return {'action': action, **self.inspect()}
        if action == 'preview':
            return self.preview(request.get('parameter'), request.get('value'), request.get('reason'), actor='ai')
        if action == 'apply':
            if request.get('change_id'):
                return self.apply(str(request['change_id']), actor='ai')
            return self.propose_and_apply(request.get('parameter'), request.get('value'), request.get('reason'), actor='ai')
        if action == 'observe':
            return self.observe(str(request.get('change_id') or ''))
        if action == 'rollback':
            return self.rollback(str(request.get('change_id') or ''), request.get('reason') or '', actor='ai')
        raise ValueError('生活参数操作类型无效')
