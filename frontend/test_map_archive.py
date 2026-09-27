import importlib.util
from pathlib import Path
from fastapi.testclient import TestClient


def test_local_archive_ranges_and_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv('MAP_DATA_DIR', str(tmp_path))
    spec = importlib.util.spec_from_file_location('map_frontend', Path(__file__).with_name('server.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with TestClient(module.app, base_url='http://localhost') as client:
        assert client.get('/map-data/region.pmtiles').status_code == 503
        (tmp_path / 'region.pmtiles').write_bytes(b'PMTiles' + bytes(range(100)))
        response = client.get('/map-data/region.pmtiles', headers={'Range': 'bytes=0-6'})
        assert response.status_code == 206
        assert response.content == b'PMTiles'
        assert response.headers['content-range'] == 'bytes 0-6/107'
        assert client.head('/map-data/region.pmtiles').status_code == 200
        assert client.get('/map-data/region.pmtiles', headers={'Range': 'bytes=999-1000'}).status_code == 416
        assert client.get('/map-data/regional.sqlite').status_code == 404
        csp = client.get('/map').headers['content-security-policy']
        assert "worker-src 'self'" in csp
        assert "connect-src 'self'" in csp
