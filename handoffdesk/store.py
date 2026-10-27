"""SQLite persistence. Every writer uses a short transaction and its own connection."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class ConflictError(ValueError):
    def __init__(self, current, proposed):
        self.current, self.proposed = current, proposed
        super().__init__("交接已被另一个窗口更新。请核对当前版本，保留或合并你的文字后再保存。")


class Store:
    def __init__(self, path):
        self.path = path
        with self.connection() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] > 4:
                raise ValueError('数据库来自更新版本，请使用相应版本打开，避免降级写入。')
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS project_state (
                    project_id TEXT PRIMARY KEY, generation INTEGER NOT NULL DEFAULT 0,
                    active_snapshot INTEGER, last_attempt TEXT, settings TEXT NOT NULL DEFAULT '{}');
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
                    generation INTEGER NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS handoffs (
                    project_id TEXT NOT NULL, revision INTEGER NOT NULL, recorded_at TEXT NOT NULL,
                    operation_id TEXT, payload TEXT NOT NULL,
                    PRIMARY KEY(project_id, revision), UNIQUE(project_id, operation_id));
                CREATE INDEX IF NOT EXISTS snapshot_project ON snapshots(project_id, id);
                CREATE TABLE IF NOT EXISTS app_preferences (
                    id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS worklines (
                    project_id TEXT NOT NULL, workline_id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(project_id,workline_id));
                CREATE TABLE IF NOT EXISTS project_selection (
                    project_id TEXT PRIMARY KEY, workline_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
                    handoff_revision INTEGER NOT NULL, snapshot_id INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL, payload TEXT NOT NULL);
            """)
            # Existing observations and handoffs remain byte-for-byte intact.
            # A default workline retains the legacy project key. Other lines use
            # an internal scope key, so late refreshes cannot cross worklines.
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(drafts)')}
            if columns and 'event_kind' not in columns:
                db.execute('ALTER TABLE drafts RENAME TO drafts_v2_migration')
            db.execute('CREATE TABLE IF NOT EXISTS drafts (project_id TEXT NOT NULL, event_kind TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(project_id,event_kind))')
            if columns and 'event_kind' not in columns:
                for row in db.execute('SELECT * FROM drafts_v2_migration').fetchall():
                    kind = json.loads(row['payload']).get('event_kind', 'handoff')
                    db.execute('INSERT INTO drafts VALUES(?,?,?,?)', (row['project_id'],kind,row['revision'],row['payload']))
                db.execute('DROP TABLE drafts_v2_migration')
            db.execute('CREATE TABLE IF NOT EXISTS file_nodes (project_id TEXT NOT NULL, node_id TEXT NOT NULL, workline_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(project_id,node_id))')
            db.execute('CREATE TABLE IF NOT EXISTS node_file_state (project_id TEXT NOT NULL, node_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(project_id,node_id))')
            db.execute('PRAGMA user_version=4')

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=10000")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _ensure(self, db, project_id):
        db.execute("INSERT OR IGNORE INTO project_state(project_id) VALUES (?)", (project_id,))

    def state(self, project_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM project_state WHERE project_id=?", (project_id,)).fetchone()
        if row is None:
            return {"generation": 0, "active_snapshot": None, "last_attempt": None, "settings": {}}
        result = dict(row)
        for key in ("last_attempt", "settings"):
            result[key] = json.loads(result[key]) if result[key] else None
        return result

    def begin_refresh(self, project_id):
        with self.connection(True) as db:
            self._ensure(db, project_id)
            db.execute("UPDATE project_state SET generation=generation+1 WHERE project_id=?", (project_id,))
            return db.execute("SELECT generation FROM project_state WHERE project_id=?", (project_id,)).fetchone()[0]

    def finish_refresh(self, project_id, generation, payload, usable=True):
        stamp = now()
        payload = dict(payload, observed_at=stamp)
        with self.connection(True) as db:
            cursor = db.execute("INSERT INTO snapshots(project_id,generation,observed_at,payload) VALUES (?,?,?,?)",
                                (project_id, generation, stamp, encode(payload)))
            sid = cursor.lastrowid
            errors = [o.get("source_id", "unknown") for o in payload.get("observations", []) if o.get("status") != "ok"]
            attempt = {"snapshot_id": sid, "observed_at": stamp, "errors": errors,
                       "message": payload.get("error"), "usable": usable}
            if usable:
                db.execute("UPDATE project_state SET active_snapshot=?,last_attempt=? WHERE project_id=? AND generation=?",
                           (sid, encode(attempt), project_id, generation))
            else:
                db.execute("UPDATE project_state SET last_attempt=? WHERE project_id=? AND generation=?",
                           (encode(attempt), project_id, generation))
        return sid

    def snapshot(self, sid, project_id=None):
        if sid is None:
            return None
        with self.connection() as db:
            row = db.execute("SELECT * FROM snapshots WHERE id=?", (sid,)).fetchone()
        if not row or (project_id and row["project_id"] != project_id):
            return None
        return dict(json.loads(row["payload"]), id=row["id"], **self.scope_identity(row["project_id"]),
                    observed_at=row["observed_at"])

    @staticmethod
    def scope_identity(scope):
        parts = scope.split('\x1f', 1)
        result = {'project_id':parts[0]}
        if len(parts) == 2:
            result['workline_id'] = parts[1]
        return result

    def current_handoff(self, project_id):
        with self.connection() as db:
            row = db.execute("SELECT payload FROM handoffs WHERE project_id=? ORDER BY revision DESC LIMIT 1",
                             (project_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def handoff_operation(self, project_id, operation_id):
        """Read a submission receipt without relying on the current revision."""
        with self.connection() as db:
            row = db.execute('SELECT payload FROM handoffs WHERE project_id=? AND operation_id=?',
                             (project_id, operation_id)).fetchone()
        return json.loads(row[0]) if row else None

    def save_agent_record(self, project_id, payload, expected_revision, operation_id,
                          request_hash, manifest_factory):
        """Commit one record and its explicit attachment manifest together.

        Unlike legacy handoffs, attachments are supplied by this request and
        therefore participate in idempotency. Check the receipt before touching
        the filesystem so retries preserve the original record after a move.
        """
        with self.connection(True) as db:
            row = db.execute('SELECT payload FROM handoffs WHERE project_id=? AND operation_id=?',
                             (project_id, operation_id)).fetchone()
            if row:
                existing = json.loads(row[0])
                if existing.get('agent_request_hash') != request_hash:
                    raise ValueError('相同操作编号已用于另一份内容，请使用新的操作编号。')
                return existing, True
            row = db.execute('SELECT payload,revision FROM handoffs WHERE project_id=? ORDER BY revision DESC LIMIT 1',
                             (project_id,)).fetchone()
            current = json.loads(row['payload']) if row else None
            revision = row['revision'] if row else 0
            if expected_revision != revision:
                raise ConflictError(current, payload)
            # No record or association has been written if validation fails.
            manifest = manifest_factory()
            result = dict(payload, file_manifest=manifest, agent_request_hash=request_hash,
                          revision=revision + 1, recorded_at=now(), **self.scope_identity(project_id))
            db.execute('INSERT INTO handoffs(project_id,revision,recorded_at,operation_id,payload) VALUES(?,?,?,?,?)',
                       (project_id, result['revision'], result['recorded_at'], operation_id, encode(result)))
            return result, False

    def save_handoff(self, project_id, payload, expected_revision, operation_id=None):
        with self.connection(True) as db:
            if operation_id:
                old = db.execute("SELECT payload FROM handoffs WHERE project_id=? AND operation_id=?",
                                 (project_id, operation_id)).fetchone()
                if old:
                    existing = json.loads(old[0])
                    # The manifest is generated from local associations at the
                    # first save. A retry must retain that first frozen list.
                    if any((existing.get(k, 'handoff') if k == 'event_kind' else existing.get(k)) != v
                           for k, v in payload.items() if k != 'file_manifest'):
                        raise ValueError("相同操作编号已用于另一份内容，请使用新的操作编号。")
                    return existing
            row = db.execute("SELECT payload,revision FROM handoffs WHERE project_id=? ORDER BY revision DESC LIMIT 1",
                             (project_id,)).fetchone()
            current = json.loads(row["payload"]) if row else None
            revision = row["revision"] if row else 0
            if expected_revision != revision:
                raise ConflictError(current, payload)
            result = dict(payload, revision=revision+1, recorded_at=now(), **self.scope_identity(project_id))
            db.execute("INSERT INTO handoffs(project_id,revision,recorded_at,operation_id,payload) VALUES (?,?,?,?,?)",
                       (project_id, result["revision"], result["recorded_at"], operation_id, encode(result)))
            return result

    def history(self, project_id):
        with self.connection() as db:
            rows = db.execute("SELECT payload FROM handoffs WHERE project_id=? ORDER BY revision DESC", (project_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def file_nodes(self, project_id):
        with self.connection() as db:
            rows = db.execute('SELECT payload FROM file_nodes WHERE project_id=? ORDER BY rowid DESC', (project_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def create_file_node(self, project_id, node):
        with self.connection(True) as db:
            db.execute('INSERT INTO file_nodes VALUES(?,?,?,?)',
                       (project_id, node['id'], node['workline_id'], encode(node)))
        return node

    def update_file_node(self, project_id, node_id, update):
        """Change only a custom node's metadata against its latest saved value."""
        with self.connection(True) as db:
            row = db.execute('SELECT payload FROM file_nodes WHERE project_id=? AND node_id=?',
                             (project_id,node_id)).fetchone()
            if row is None:
                raise ValueError('自定义节点不存在，请刷新节点列表。')
            node = update(json.loads(row[0]))
            db.execute('UPDATE file_nodes SET payload=? WHERE project_id=? AND node_id=?',
                       (encode(node),project_id,node_id))
        return node

    def node_file_state(self, project_id, node_id):
        with self.connection() as db:
            row = db.execute('SELECT payload FROM node_file_state WHERE project_id=? AND node_id=?',
                             (project_id,node_id)).fetchone()
        return json.loads(row[0]) if row else {'additions': [], 'hidden': []}

    def update_node_files(self, project_id, node_id, update):
        """Merge associations while holding a database write transaction."""
        with self.connection(True) as db:
            row = db.execute('SELECT payload FROM node_file_state WHERE project_id=? AND node_id=?',
                             (project_id,node_id)).fetchone()
            state = json.loads(row[0]) if row else {'additions': [], 'hidden': []}
            state = update(state)
            db.execute('INSERT INTO node_file_state VALUES(?,?,?) ON CONFLICT(project_id,node_id) DO UPDATE SET payload=excluded.payload',
                       (project_id,node_id,encode(state)))
        return state

    def set_setting(self, project_id, key, value):
        with self.connection(True) as db:
            self._ensure(db, project_id)
            row = db.execute("SELECT settings FROM project_state WHERE project_id=?", (project_id,)).fetchone()
            settings = json.loads(row[0])
            settings[key] = value
            # Invalidate a pending refresh and its old focus; observations stay in history.
            db.execute("UPDATE project_state SET settings=?,generation=generation+1,active_snapshot=NULL,last_attempt=NULL WHERE project_id=?",
                       (encode(settings), project_id))

    def preferences(self):
        with self.connection() as db:
            row = db.execute('SELECT payload FROM app_preferences WHERE id=1').fetchone()
        return json.loads(row[0]) if row else {}

    def save_preferences(self, patch):
        with self.connection(True) as db:
            row = db.execute('SELECT payload FROM app_preferences WHERE id=1').fetchone()
            value = json.loads(row[0]) if row else {}
            if 'entries' in patch:
                value['entries'] = dict(value.get('entries', {}), **patch['entries'])
            value.update({k:v for k,v in patch.items() if k != 'entries'})
            db.execute('INSERT INTO app_preferences(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (encode(value),))
        return value

    def load_draft(self, project_id, event_kind='handoff'):
        with self.connection() as db:
            row = db.execute('SELECT payload FROM drafts WHERE project_id=? AND event_kind=?', (project_id,event_kind)).fetchone()
        return json.loads(row[0]) if row else None

    def save_draft(self, project_id, payload, expected_revision):
        kind = payload.get('event_kind', 'handoff')
        with self.connection(True) as db:
            for other in db.execute('SELECT payload FROM drafts WHERE project_id=? AND event_kind<>?', (project_id,kind)):
                if json.loads(other[0]).get('draft_id') == payload['draft_id']:
                    raise ValueError('同一草稿不能改变记录类型；请为另一类记录建立独立草稿。')
            row = db.execute('SELECT revision,payload FROM drafts WHERE project_id=? AND event_kind=?', (project_id,kind)).fetchone()
            current = json.loads(row['payload']) if row else None
            revision = row['revision'] if row else 0
            if revision != expected_revision or (current and current['draft_id'] != payload['draft_id']):
                raise ConflictError(current, payload)
            if current and any(current.get(key) != payload.get(key) for key in ('base_revision','source_snapshot_id','event_kind')):
                raise ValueError('同一草稿的原始版本、来源快照和记录类型不能变更。请保留原依据，或明确丢弃后新建。')
            saved = dict(payload, revision=revision+1, updated_at=now(), **self.scope_identity(project_id))
            db.execute('INSERT INTO drafts(project_id,event_kind,revision,payload) VALUES(?,?,?,?) ON CONFLICT(project_id,event_kind) DO UPDATE SET revision=excluded.revision,payload=excluded.payload',
                       (project_id,kind,saved['revision'], encode(saved)))
        return saved

    def delete_draft(self, project_id, draft_id, expected_revision, event_kind=None):
        with self.connection(True) as db:
            rows = db.execute('SELECT event_kind,revision,payload FROM drafts WHERE project_id=?', (project_id,)).fetchall()
            for row in rows:
                if event_kind is not None and row['event_kind'] != event_kind:
                    continue
                if row['revision'] == expected_revision and json.loads(row['payload'])['draft_id'] == draft_id:
                    db.execute('DELETE FROM drafts WHERE project_id=? AND event_kind=?', (project_id,row['event_kind']))
                    return True
        return False

    def worklines(self, project_id):
        with self.connection() as db:
            rows = db.execute('SELECT payload FROM worklines WHERE project_id=? ORDER BY rowid', (project_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_workline(self, project_id, value):
        with self.connection(True) as db:
            row = db.execute('SELECT payload FROM worklines WHERE project_id=? AND workline_id=?', (project_id,value['id'])).fetchone()
            previous = json.loads(row[0]) if row else {}
            result = dict(previous, **value)
            db.execute('INSERT INTO worklines VALUES(?,?,?) ON CONFLICT(project_id,workline_id) DO UPDATE SET payload=excluded.payload',
                       (project_id,result['id'],encode(result)))
        return result

    def selected_workline(self, project_id):
        with self.connection() as db:
            row = db.execute('SELECT workline_id FROM project_selection WHERE project_id=?', (project_id,)).fetchone()
        return row[0] if row else 'default'

    def select_workline(self, project_id, workline_id):
        with self.connection(True) as db:
            db.execute('INSERT INTO project_selection VALUES(?,?) ON CONFLICT(project_id) DO UPDATE SET workline_id=excluded.workline_id', (project_id,workline_id))

    def review_history(self, project_id):
        with self.connection() as db:
            rows = db.execute('SELECT payload FROM reviews WHERE project_id=? ORDER BY id DESC', (project_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_review(self, project_id, expected_revision, snapshot_id):
        with self.connection(True) as db:
            row = db.execute('SELECT revision,payload FROM handoffs WHERE project_id=? ORDER BY revision DESC LIMIT 1', (project_id,)).fetchone()
            current = json.loads(row['payload']) if row else None
            if (row['revision'] if row else 0) != expected_revision:
                raise ConflictError(current, {'snapshot_id':snapshot_id})
            state = db.execute('SELECT active_snapshot,last_attempt FROM project_state WHERE project_id=?', (project_id,)).fetchone()
            attempt = json.loads(state['last_attempt']) if state and state['last_attempt'] else {}
            if not state or state['active_snapshot'] != snapshot_id or attempt.get('snapshot_id') != snapshot_id:
                raise ValueError('来源已更新，请重新查看变化后复核。')
            if attempt.get('errors') or attempt.get('message') or not attempt.get('usable'):
                raise ValueError('仍有来源读取失败，请解决后再确认复核。')
            stamp = now()
            value = dict(handoff_revision=expected_revision, snapshot_id=snapshot_id, recorded_at=stamp,
                         **self.scope_identity(project_id))
            cursor = db.execute('INSERT INTO reviews(project_id,handoff_revision,snapshot_id,recorded_at,payload) VALUES(?,?,?,?,?)',
                                (project_id,expected_revision,snapshot_id,stamp,encode(value)))
            value['id'] = cursor.lastrowid
            db.execute('UPDATE reviews SET payload=? WHERE id=?', (encode(value),value['id']))
        return value
