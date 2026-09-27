from uuid import uuid4

from app.chat import ChatStore
from app.config import Settings
from app.domain.call import CallContext, CallStatus
from app.asterisk.call_manager import CallManager


def test_unclean_restart_has_durable_reason(tmp_path):
    cfg = Settings(_env_file=None, outbox_dir=tmp_path/'out', recording_dir=tmp_path/'rec')
    chat = ChatStore(cfg.outbox_dir/'chat.sqlite3')
    ctx = CallContext(uuid4(), '201', status=CallStatus.active)
    chat.create(ctx, 'auto')
    chat.db.close()
    manager = CallManager(cfg)
    snapshot = manager.get(ctx.call_id)
    assert snapshot['status'] == 'failed'
    assert snapshot['reason'] == 'service_restart'
    assert snapshot['session_id'] == str(ctx.session_id)
    manager.chat.db.close()


def test_normal_hangup_is_not_reclassified_after_restart(tmp_path):
    cfg = Settings(_env_file=None, outbox_dir=tmp_path/'out', recording_dir=tmp_path/'rec')
    chat = ChatStore(cfg.outbox_dir/'chat.sqlite3')
    ctx = CallContext(uuid4(), '201', status=CallStatus.active)
    chat.create(ctx, 'auto')
    chat.update(ctx.call_id, status='ended', reason='remote_hangup')
    chat.db.close()
    manager = CallManager(cfg)
    assert manager.get(ctx.call_id)['reason'] == 'remote_hangup'
    manager.chat.db.close()
