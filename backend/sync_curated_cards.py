"""Add reviewed DDS prefill to unchanged, already published ticket scenarios.

Run inside the Backend container with --apply after deploying a new ticket catalog.
Teacher edits and existing prefilled cards are never replaced.
"""
import argparse
import json
from pathlib import Path

from server import Scenario, store
from workspace import prefilled_from_scenario


def sync(apply: bool = False) -> dict[str, int]:
    catalog = json.loads((Path(__file__).parent / 'data' / 'tickets.json').read_text(encoding='utf-8'))
    drafts = {item['id']: item['scenario'] for item in catalog['drafts']
              if item['scenario'].get('prefilled_card')}
    result = {'eligible': 0, 'updated': 0, 'skipped_edited': 0, 'not_published': 0}
    for draft_id, draft in drafts.items():
        rows = store.db.execute('SELECT scenario_id FROM ticket_publications WHERE draft_id=?',
                                (draft_id,)).fetchall()
        if not rows:
            result['not_published'] += 1
        for (scenario_id,) in rows:
            current = store.scenario(scenario_id)
            # A changed scenario is a teacher-authored artifact; don't infer
            # that the newly curated source should override its decisions.
            if (not current or current.get('prefilled_card')
                    or any(current.get(key) != draft.get(key)
                           for key in ('title', 'incident', 'location', 'owner_service',
                                       'updates', 'dds_expectation'))):
                result['skipped_edited'] += 1
                continue
            updated = {**current, 'prefilled_card': draft['prefilled_card']}
            prefilled_from_scenario(updated)
            result['eligible'] += 1
            if apply:
                store.put_scenario(Scenario.model_validate(updated))
                result['updated'] += 1
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='write eligible cards; default is dry-run')
    args = parser.parse_args()
    print(json.dumps(sync(args.apply), ensure_ascii=False))
