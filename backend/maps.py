"""Read-only regional cartography, served by the existing Backend."""
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from contextlib import closing

from fastapi import APIRouter, Depends, HTTPException, Query


def connect():
    folder = Path(os.getenv('MAP_DATA_DIR', str(Path(__file__).resolve().parents[1]/'deploy/maps')))
    path = folder/'regional.sqlite'
    if not path.is_file():
        raise HTTPException(503, 'Пакет карты не установлен')
    db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    deadline = time.monotonic()+3
    db.set_progress_handler(lambda: time.monotonic() > deadline, 10000)
    return db


def manifest():
    with closing(connect()) as db:
        return json.loads(db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])


def features(west, south, east, north, zoom):
    if not all(math.isfinite(v) for v in (west,south,east,north)) or not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise HTTPException(422, 'Некорректные границы карты')
    with closing(connect()) as db:
        rows = db.execute('''SELECT f.kind,f.name,f.geometry FROM bounds b JOIN features f ON f.id=b.id
            WHERE b.east>=? AND b.west<=? AND b.north>=? AND b.south<=? AND f.min_zoom<=?
            ORDER BY f.min_zoom,f.id LIMIT 8001''', (west,east,south,north,zoom)).fetchall()
    result = []
    for kind, name, geometry in rows[:8000]:
        points = json.loads(geometry)
        if zoom < 14 and len(points) > 30:
            # Simplify only the small-scale display; source geometry stays intact.
            points = points[::max(1,len(points)//30)] + [points[-1]]
        result.append({'kind':kind,'name':name,'coordinates':points})
    return {'features':result,'truncated':len(rows)>8000}


def search(query):
    tokens = re.findall(r'[\w]+', query.lower().replace('ё','е'), flags=re.UNICODE)[:12]
    tokens = [t for t in tokens if t not in ('ул','улица','дом','д','г','город')]
    if not tokens:
        return {'results':[]}
    expression = ' AND '.join('"'+token+'"*' for token in tokens)
    with closing(connect()) as db:
        rows = db.execute('''SELECT a.label,a.latitude,a.longitude FROM address_search s
            JOIN addresses a ON a.id=s.rowid WHERE address_search MATCH ? ORDER BY rank LIMIT 30''', (expression,)).fetchall()
    return {'results':[{'label':label,'latitude':lat,'longitude':lon} for label,lat,lon in rows]}


def router(accounts, authorize):
    api = APIRouter(prefix='/api/v1/student/map', dependencies=[Depends(authorize),Depends(accounts.require('student'))])

    @api.get('/manifest')
    def metadata():
        return manifest()

    @api.get('/features')
    def viewport(west:float,south:float,east:float,north:float,zoom:int=Query(15,ge=2,le=19)):
        try:
            return features(west,south,east,north,zoom)
        except sqlite3.OperationalError:
            raise HTTPException(503,'Не удалось загрузить участок карты. Увеличьте масштаб.')

    @api.get('/search')
    def addresses(q:str=Query(min_length=2,max_length=160)):
        try:
            return search(q)
        except sqlite3.OperationalError:
            raise HTTPException(503,'Адресный поиск временно недоступен')

    return api
