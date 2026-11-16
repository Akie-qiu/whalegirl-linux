"""Small persistent UI circuit breaker, independent of model/API failures."""
import json
import time

MESSAGES={
    'unowned_editor':'微信里有未完成的朋友圈编辑，已保留；自动发布暂缓，缓存读取继续。',
    'editor_job_active':'上一项朋友圈编辑仍在执行，稍后再检查；不会重复提交。',
    'outcome_uncertain':'上一项朋友圈提交结果需要核对，暂不操作编辑器或重复发布。',
    'cleanup_failed':'朋友圈窗口退出未完成，后续操作已暂缓；稍后核对并恢复本程序的编辑器。',
    'file_picker':'朋友圈选图界面暂不可用，文案和配图已保留，稍后重查。',
}


class MomentsUiHealth:
    def __init__(self,store):
        self.store=store
        with store.lock,store.db:
            store.db.execute('CREATE TABLE IF NOT EXISTS moments_ui_health(account TEXT PRIMARY KEY,value TEXT NOT NULL)')

    def status(self,account):
        row=self.store.db.execute('SELECT value FROM moments_ui_health WHERE account=?',(account,)).fetchone()
        return json.loads(row[0]) if row else {'state':'unknown','retry_at':0,'failures':0}

    def record(self,account,code=''):
        now=time.time()
        with self.store.lock,self.store.db:
            previous=self.status(account)
            failures=previous.get('failures',0)+1 if code else 0
            value={'state':'blocked' if code else 'ready','code':code,'message':MESSAGES.get(code,MESSAGES['cleanup_failed']) if code else '',
                   'failures':failures,'checked_at':now,'retry_at':now+min(300,30*2**min(4,failures-1)) if code else 0}
            self.store.db.execute('INSERT OR REPLACE INTO moments_ui_health VALUES(?,?)',(account,json.dumps(value)))
        return value

    @staticmethod
    def error_code(error):
        value=str(error)
        if any(term in value for term in ('existing editor','preserve the draft','unowned editor')):return 'unowned_editor'
        if 'image file' in value or 'file picker' in value:return 'file_picker'
        if 'cleanup' in value or 'owned editor did not close' in value:return 'cleanup_failed'
        return ''
