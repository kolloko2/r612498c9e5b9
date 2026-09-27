"""Actual student speech/input evidence, independent of DDS card delivery mode."""
import json


def summary(store, value):
    counts = {'text': 0, 'sip': 0}
    messages = store.load(value['id']).get('messages', [])
    counts[value.get('transport', 'text')] = sum(m.get('role') == 'user' for m in messages)
    briefings = []
    if getattr(store, 'briefings_available', False):
        for (raw,) in store.db.execute('SELECT body FROM briefings WHERE session_id=?', (value['id'],)).fetchall():
            item = json.loads(raw)
            mode = item.get('transport', 'text')
            rows = store.load(item['id']).get('messages', []) if mode == 'sip' else item.get('messages', [])
            turns = sum(m.get('role') == 'user' for m in rows)
            counts[mode] += turns
            briefings.append({'id': item['id'], 'transport': mode, 'state': item['state'],
                              'student_turns': turns, 'recipient': item.get('destination') or item.get('service')})
    # Questions during incoming brigade reports are real telephone speech too.
    for call in [*(value.get('field_report_calls') or {}).values(), value.get('progress_call') or {}]:
        if call.get('session_id'):
            counts['sip'] += sum(m.get('role') == 'user' for m in store.load(call['session_id']).get('messages', []))
    used = [mode for mode, count in counts.items() if count]
    return {'mode': 'mixed' if len(used) == 2 else used[0] if used else 'none',
            'text_turns': counts['text'], 'sip_turns': counts['sip'], 'briefings': briefings,
            'text_input_allowed': value.get('text_input_allowed', True)}
