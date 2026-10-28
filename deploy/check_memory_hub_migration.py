"""Run the real migration only on an explicitly private SQLite backup."""
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

copy=Path(sys.argv[1]).resolve()
if Path('/opt/whalegirl-linux-assets') not in copy.parents or copy.name!='migration-check.sqlite3':
    raise RuntimeError('Migration rehearsal must use its private backup path')
sys.path.insert(0,sys.argv[3])
from whalegirl_linux.store import Store
from whalegirl_linux.memory import MemoryBook
from whalegirl_linux.learning import LearningBook
from whalegirl_linux.affection import AffectionBook
from whalegirl_linux.conversation import ensure_persona_tables
from whalegirl_linux.settings import Settings

with sqlite3.connect('file:'+str(copy)+'?mode=ro',uri=True) as db:
    original={r[0]:(r[1],r[2]) for r in db.execute('SELECT id,content,created FROM memories')}
    revisions={r[0] for r in db.execute('SELECT id FROM memory_revisions')}
    learning={r[0] for r in db.execute('SELECT id FROM learning_proposals')}
    learning_revisions=db.execute('SELECT count(*) FROM learning_revisions').fetchone()[0]
store=Store(copy)
book=MemoryBook(store);hub=book.hub
hub.config=SimpleNamespace(settings=Settings.model_validate(json.loads(Path(sys.argv[2]).read_text())))
LearningBook(store);AffectionBook(store);ensure_persona_tables(store)
hub.migrate();hub.migrate_profiles();hub.migrate_masks()
def counts():return tuple(store.db.execute('SELECT count(*) FROM '+n).fetchone()[0] for n in ('memories','memory_revisions','memory_origins','memory_evidence'))
first=counts();hub.migrate();hub.migrate_profiles();hub.migrate_masks()
if counts()!=first:raise RuntimeError('Migration is not idempotent')
current={r[0]:(r[1],r[2]) for r in store.db.execute('SELECT id,content,created FROM memories')}
if any(current.get(k)!=v for k,v in original.items()):raise RuntimeError('Existing memory identity/content changed')
if not revisions.issubset({r[0] for r in store.db.execute('SELECT id FROM memory_revisions')}):raise RuntimeError('Existing revisions missing')
if learning!={r[0] for r in store.db.execute('SELECT id FROM learning_proposals')}:raise RuntimeError('Learning rows changed')
if learning_revisions>store.db.execute('SELECT count(*) FROM learning_revisions').fetchone()[0]:raise RuntimeError('Learning history missing')
mapped={int(r[0]) for r in store.db.execute("SELECT origin_id FROM memory_origins WHERE source='learning'")}
if mapped!=learning:raise RuntimeError('Incomplete legacy learning mapping')
report={'old_memories':len(original),'unified_memories':first[0],'old_revisions':len(revisions),
        'unified_revisions':first[1],'learning_rows':len(learning),'mapped_learning_rows':len(mapped),
        'origins':first[2],'evidence_links':first[3],'idempotent':True,'old_ids_and_content_preserved':True,
        'tiers':{r[0]:r[1] for r in store.db.execute('SELECT tier,count(*) FROM memories GROUP BY tier')},
        'states':{r[0]:r[1] for r in store.db.execute('SELECT status,count(*) FROM memories GROUP BY status')}}
store.db.close()
print(json.dumps(report))
