import pytest
from pydantic import ValidationError
from workspace import Card
from test_rbac_integration import classroom


@pytest.mark.parametrize('values', [
    {'latitude': 91, 'longitude': 0}, {'latitude': 0, 'longitude': -181},
    {'latitude': 1}, {'longitude': 1}, {'latitude': float('inf'), 'longitude': 1},
])
def test_invalid_coordinates(values):
    with pytest.raises(ValidationError):
        Card(**values)


def test_coordinates_persist_independently_of_address(classroom):
    c = classroom
    client, headers, session = c['client'], c['headers']['student1'], c['session']
    path = '/api/v1/student/sessions/' + session['id']
    card = {**session['card'], 'latitude': 0, 'longitude': 0, 'street': 'Учебная',
            'emergency': True, 'important': True, 'bookmarked': True}
    response = client.put(path + '/card', headers=headers, json={'revision': 0, 'card': card})
    assert response.status_code == 200, response.text
    saved = client.get(path, headers=headers).json()
    assert saved['card']['latitude'] == 0 and saved['card']['longitude'] == 0
    assert saved['card']['street'] == 'Учебная'
    assert saved['card']['emergency'] and saved['card']['bookmarked']
    assert saved['registration']['operator']
    assert client.get(path, headers=c['headers']['student2']).status_code == 404
    assert saved['events'][-1]['detail']['latitude']['after'] == 0
