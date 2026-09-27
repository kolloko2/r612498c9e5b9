"""Source-checked incoming DDS cards from the supplied ticket catalog."""
import json
from pathlib import Path

from server import Scenario
from workspace import Card, prefilled_from_scenario
from routing import preview


def test_curated_cards_are_complete_and_route_from_exact_classifier():
    catalog = json.loads((Path(__file__).parent / 'data' / 'tickets.json').read_text(encoding='utf-8'))
    curated = json.loads((Path(__file__).parent.parent / 'tools' / 'data' /
                          'dds_prefilled_cards.json').read_text(encoding='utf-8'))
    drafts = {item['id']: item for item in catalog['drafts']}
    assert len(curated) == 10
    for draft_id, source in curated.items():
        scenario = Scenario.model_validate(drafts[draft_id]['scenario']).model_dump()
        card = Card.model_validate(prefilled_from_scenario(scenario)).model_dump()
        assert source['source_address'] == scenario['location'] == card['address_note']
        assert card['classifier_id'] == source['classifier_id']
        assert card['incident_type'] and card['classifier_features']
        assert card['services'] and scenario['owner_service'] in card['services']
        assert scenario['dds_expectation']['brief_service'] == scenario['owner_service']
        assert preview(card)['source_row'] is not None
        assert len(scenario['updates']) >= 4
