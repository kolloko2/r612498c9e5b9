"""Build an offline, spatially indexed OSM package; never query OSM at runtime.

python tools/prepare_regional_map.py --download
Requires osmium>=4,<5 only on the preparation machine.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://download.openstreetmap.fr/extracts/russia/central_federal_district/'
REGIONS = {'moscow': 'Москва', 'moscow_oblast': 'Московская область'}


def download(folder, region):
    target = folder / (region + '.osm.pbf')
    if target.exists():
        return target
    partial = target.with_suffix('.part')
    request = urllib.request.Request(BASE + region + '-latest.osm.pbf',
                                    headers={'User-Agent': 'Trainer112-map-preparation/2.0'})
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=120) as source, partial.open('wb') as output:
        size = 0
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > 2 * 1024**3:
                raise RuntimeError('Regional source exceeds 2 GiB')
            digest.update(chunk)
            output.write(chunk)
    partial.replace(target)
    print(f'Downloaded {region}: {size} bytes, sha256={digest.hexdigest()}', flush=True)
    return target


def build(inputs, output):
    import osmium
    output.parent.mkdir(parents=True, exist_ok=True)
    # A new package is built separately; an incomplete import never replaces live data.
    temporary = output.with_name(output.name + f'.{time.time_ns()}.tmp')
    db = sqlite3.connect(temporary)
    db.executescript('''PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE features(id INTEGER PRIMARY KEY,osm_id INTEGER UNIQUE,kind TEXT,name TEXT,min_zoom INTEGER,geometry TEXT);
        CREATE VIRTUAL TABLE bounds USING rtree(id,west,east,south,north);
        CREATE TABLE addresses(id INTEGER PRIMARY KEY,osm_key TEXT UNIQUE,label TEXT,latitude REAL,longitude REAL);
        CREATE VIRTUAL TABLE address_search USING fts5(label,content='addresses',content_rowid='id',tokenize='unicode61');
    ''')
    count = 0
    class Importer(osmium.SimpleHandler):
        def __init__(self, region):
            super().__init__()
            self.region = region

        def address(self, key, tags, lat, lon):
            street = tags.get('addr:street') or tags.get('addr:place')
            house = tags.get('addr:housenumber')
            if not street or not house:
                return
            place = tags.get('addr:city') or tags.get('addr:suburb') or REGIONS.get(self.region, self.region)
            label = ', '.join([place, street, house])
            db.execute('INSERT OR IGNORE INTO addresses(osm_key,label,latitude,longitude) VALUES(?,?,?,?)',
                       (key, label, lat, lon))

        def node(self, node):
            if node.location.valid():
                self.address('n'+str(node.id), node.tags, node.location.lat, node.location.lon)

        def way(self, way):
            nonlocal count
            tags = way.tags
            road, building = tags.get('highway'), tags.get('building')
            water = tags.get('natural') == 'water' or tags.get('waterway')
            park = tags.get('leisure') in ('park', 'garden') or tags.get('landuse') in ('forest', 'grass', 'meadow')
            if not (road or building or water or park or tags.get('addr:housenumber')):
                return
            coords = [[round(n.lon, 7), round(n.lat, 7)] for n in way.nodes if n.location.valid()]
            if len(coords) < 2:
                return
            self.address('w'+str(way.id), tags, sum(p[1] for p in coords)/len(coords), sum(p[0] for p in coords)/len(coords))
            if not (road or building or water or park):
                return
            kind = 'road' if road else 'building' if building else 'water' if water else 'park'
            zoom = 8 if road in ('motorway', 'trunk', 'primary') else 10 if road in ('secondary', 'tertiary') else 14 if road else 16 if building else 11
            cursor = db.execute('INSERT OR IGNORE INTO features(osm_id,kind,name,min_zoom,geometry) VALUES(?,?,?,?,?)',
                (way.id, kind, tags.get('name', ''), zoom, json.dumps(coords, separators=(',', ':'))))
            if cursor.rowcount:
                xs, ys = [p[0] for p in coords], [p[1] for p in coords]
                db.execute('INSERT INTO bounds VALUES(?,?,?,?,?)', (cursor.lastrowid, min(xs), max(xs), min(ys), max(ys)))
                count += 1
                if count % 100000 == 0:
                    db.commit()
                    print(f'Indexed {count} features', flush=True)

    try:
        for region, path in inputs:
            print(f'Importing {region}', flush=True)
            Importer(region).apply_file(os.path.relpath(path), locations=True, idx='flex_mem')
        db.execute("INSERT INTO address_search(address_search) VALUES('rebuild')")
        db.execute('CREATE INDEX IF NOT EXISTS features_zoom ON features(min_zoom)')
        extent = db.execute('SELECT min(south),min(west),max(north),max(east) FROM bounds').fetchone()
        meta = {'title': ' / '.join(REGIONS.get(r, r) for r, _ in inputs), 'bbox': list(extent),
                'features': count, 'addresses': db.execute('SELECT count(*) FROM addresses').fetchone()[0],
                'prepared_at': int(time.time()), 'source': BASE, 'license': 'ODbL 1.0',
                'attribution': '© OpenStreetMap contributors',
                'sources': [{'region': r, 'sha256': hashlib.file_digest(p.open('rb'), 'sha256').hexdigest()} for r, p in inputs]}
        db.execute('INSERT INTO metadata VALUES(?,?)', ('manifest', json.dumps(meta, ensure_ascii=False)))
        db.commit()
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('Package integrity failure')
    finally:
        db.close()
    os.replace(temporary, output)
    print(json.dumps(meta, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--regions', nargs='+', choices=list(REGIONS), default=list(REGIONS))
    parser.add_argument('--output', type=Path, default=ROOT/'deploy/maps/regional.sqlite')
    args = parser.parse_args()
    folder = ROOT/'deploy/maps/sources'
    folder.mkdir(parents=True, exist_ok=True)
    inputs = [(r, download(folder, r) if args.download else folder/(r+'.osm.pbf')) for r in args.regions]
    build(inputs, args.output)


if __name__ == '__main__':
    main()
