"""Download pinned browser assets and an offline Protomaps style (build-time only).

Run from repository root. Tile preparation is separate: see docs/MAPS.md.
No runtime CDN is used. Existing regional.sqlite remains the address-search index.
"""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'frontend/assets/maplibre'
TEMP = ROOT / 'tmp/maplibre-setup'
MAPLIBRE = '5.18.0'
PMTILES = '4.5.0'
STYLE = '5.7.2'
ASSETS_REVISION = '028c18f713baecad011301ff7a69acc39bcc2ae7'


def download(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=180) as response:
        data = response.read()
    target.write_bytes(data)
    return {'file': str(target.relative_to(TARGET)), 'source': url,
            'sha256': hashlib.sha256(data).hexdigest()}


def main():
    TARGET.mkdir(parents=True, exist_ok=True)
    TEMP.mkdir(parents=True, exist_ok=True)
    urls = {
        'maplibre-gl.js': f'https://unpkg.com/maplibre-gl@{MAPLIBRE}/dist/maplibre-gl-csp.js',
        'maplibre-gl-csp-worker.js': f'https://unpkg.com/maplibre-gl@{MAPLIBRE}/dist/maplibre-gl-csp-worker.js',
        'maplibre-gl.css': f'https://unpkg.com/maplibre-gl@{MAPLIBRE}/dist/maplibre-gl.css',
        'maplibre-LICENSE.txt': f'https://unpkg.com/maplibre-gl@{MAPLIBRE}/LICENSE.txt',
        'pmtiles.js': f'https://unpkg.com/pmtiles@{PMTILES}/dist/pmtiles.js',
        'pmtiles-LICENSE': 'https://raw.githubusercontent.com/protomaps/PMTiles/main/LICENSE',
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda pair: download(pair[1], TARGET / pair[0]), urls.items()))
    # Pin the asset repository revision so fonts and sprites reproduce exactly.
    revision = ASSETS_REVISION
    archive = TEMP / 'basemaps-assets.zip'
    urllib.request.urlretrieve(f'https://github.com/protomaps/basemaps-assets/archive/{revision}.zip', archive)
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            relative = '/'.join(info.filename.split('/')[1:])
            if info.is_dir() or not (relative.startswith('fonts/') or relative in (
                    'sprites/v4/light.json', 'sprites/v4/light.png',
                    'sprites/v4/light@2x.json', 'sprites/v4/light@2x.png', 'sprites/LICENSE.md')):
                continue
            destination = (TARGET / relative).resolve()
            if not destination.is_relative_to(TARGET.resolve()):
                raise ValueError('Unsafe archive path')
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(bundle.read(info))
    url = f'https://npm-style.protomaps.dev/style.json?version={STYLE}&theme=light&tiles=20260927&lang=ru'
    with urllib.request.urlopen(url, timeout=60) as response:
        style = json.load(response)
    style['glyphs'] = '/assets/maplibre/fonts/{fontstack}/{range}.pbf'
    style['sprite'] = '/assets/maplibre/sprites/v4/light'
    for source in style['sources'].values():
        if source['type'] != 'vector':
            raise ValueError('Unexpected basemap source')
        source['url'] = 'pmtiles:///map-data/region.pmtiles'
    (TARGET / 'style.json').write_text(json.dumps(style, ensure_ascii=False), encoding='utf-8')
    (TARGET / 'provenance.json').write_text(json.dumps({
        'maplibre': MAPLIBRE, 'pmtiles': PMTILES, 'style': STYLE,
        'assets_revision': revision, 'files': records,
        'style_source': url, 'attribution': 'Protomaps; OpenStreetMap contributors, ODbL 1.0',
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Offline MapLibre assets prepared:', TARGET)


if __name__ == '__main__':
    main()
