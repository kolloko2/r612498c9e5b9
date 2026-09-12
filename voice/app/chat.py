import json
import sqlite3
from datetime import datetime, timezone


class ChatStore:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS integration_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT NOT NULL, body TEXT NOT NULL)')
        self.db.execute('CREATE INDEX IF NOT EXISTS events_call_seq ON integration_events(call_id,seq)')
        self.db.commit()
        # An unclean service restart must not leave old sessions appearing active.
        for cid, body in self.db.execute('SELECT id,body FROM chats').fetchall():
            chat = json.loads(body)
            if chat['status'] not in ('ended', 'failed'):
                chat['status'] = 'failed'
                for m in chat['messages']:
                    if m['status'] in ('queued', 'playing', 'recognizing'):
                        m['status'] = 'error'
                self.save(chat, "call.updated", {"status": "failed", "reason": "service_restart"})

    def save(self, chat, kind=None, payload=None):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO chats VALUES (?,?)', (chat['call_id'], json.dumps(chat, ensure_ascii=False)))
            if kind:
                self.event(chat, kind, payload)

    def get(self, cid):
        row = self.db.execute('SELECT body FROM chats WHERE id=?', (str(cid),)).fetchone()
        return json.loads(row[0]) if row else None

    def event(self, chat, kind, payload):
        body = {'call_id': chat['call_id'], 'session_id': chat['session_id'], 'type': kind,
                'time': datetime.now(timezone.utc).isoformat(), 'payload': payload}
        self.db.execute('INSERT INTO integration_events(call_id,body) VALUES (?,?)',
                        (chat['call_id'], json.dumps(body, ensure_ascii=False)))

    def events(self, cid, after=0):
        return [dict(json.loads(body), event_id=str(seq)) for seq, body in self.db.execute(
            'SELECT seq,body FROM integration_events WHERE call_id=? AND seq>? ORDER BY seq LIMIT 200', (str(cid), after))]

    def create(self, ctx, mode, scenario_id=None):
        self.save({'call_id': str(ctx.call_id), 'session_id': str(ctx.session_id), 'status': ctx.status.value,
                   'mode': mode, 'scenario_id': scenario_id,
                   'created_at': datetime.now(timezone.utc).isoformat(), 'messages': []},
                  'call.created', {'status': ctx.status.value, 'mode': mode, 'scenario_id': scenario_id})

    def update(self, cid, **values):
        chat = self.get(cid)
        if chat:
            chat.update(values)
            self.save(chat, 'call.updated', values)

    def message(self, cid, mid, **values):
        chat = self.get(cid)
        if not chat:
            return
        message = next((m for m in chat['messages'] if m['id'] == str(mid)), None)
        if message is None:
            message = {'id': str(mid), 'call_id': str(cid), 'session_id': chat['session_id'],
                       'time': datetime.now(timezone.utc).isoformat(), 'text': '', 'role': 'operator', 'status': 'queued'}
            chat['messages'].append(message)
        message.update(values)
        status = message['status']
        kind = ('operator.final' if status == 'recognized' else 'operator.partial' if status == 'recognizing'
                else 'operator.error') if message['role'] == 'me' else 'playback.' + status
        self.save(chat, kind, {**message, 'role': 'operator' if message['role'] == 'me' else 'victim'})

    def recent(self):
        rows = self.db.execute('SELECT body FROM chats ORDER BY rowid DESC LIMIT 100').fetchall()
        return [{k: v for k, v in json.loads(r[0]).items() if k != 'messages'} for r in rows]
