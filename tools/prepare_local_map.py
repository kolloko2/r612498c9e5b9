"""Download a bounded public OSM extract once; runtime map/geocoder remain offline."""
import json
from pathlib import Path
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
BBOX=[55.74,37.58,55.78,37.65]


def main():
    folder=ROOT/'frontend/assets/map-data';folder.mkdir(exist_ok=True)
    query='[out:json][timeout:90];(way[highway](55.74,37.58,55.78,37.65);way[building](55.74,37.58,55.78,37.65);nwr["addr:housenumber"](55.74,37.58,55.78,37.65););out geom;'
    request=urllib.request.Request('https://overpass-api.de/api/interpreter',
        data=urllib.parse.urlencode({'data':query}).encode(),headers={'User-Agent':'Trainer112-local-map-preparation/1.0'})
    with urllib.request.urlopen(request,timeout=120) as response:
        raw=response.read(32*1024*1024+1)
    if len(raw)>32*1024*1024:raise RuntimeError('Extract too large')
    data=json.loads(raw);features=[];addresses=[];seen=set()
    if data.get('remark'):raise RuntimeError('Overpass did not complete the extract')
    for item in data['elements']:
        tags=item.get('tags',{});geometry=item.get('geometry',[])
        coords=[[p['lon'],p['lat']] for p in geometry if 'lat' in p]
        if len(coords)>1 and ('highway' in tags or 'building' in tags):
            features.append({'kind':'building' if 'building' in tags else 'road',
                             'name':tags.get('name',''),'coordinates':coords,
                             'bbox':[min(p[0] for p in coords),min(p[1] for p in coords),max(p[0] for p in coords),max(p[1] for p in coords)]})
        if 'addr:housenumber' in tags and ('addr:street' in tags or 'addr:place' in tags):
            latitude=item.get('lat');longitude=item.get('lon')
            if latitude is None and coords:
                latitude=sum(p[1] for p in coords)/len(coords);longitude=sum(p[0] for p in coords)/len(coords)
            if latitude is None:continue
            street=tags.get('addr:street',tags.get('addr:place',''));house=tags['addr:housenumber']
            label=f'Москва, {street}, {house}'
            if label in seen:continue
            seen.add(label);addresses.append({'label':label,'street':street,'house':house,'city':'Москва',
                                             'latitude':latitude,'longitude':longitude})
    result={'source':'https://www.openstreetmap.org/copyright','license':'ODbL 1.0',
            'attribution':'© OpenStreetMap contributors','timestamp':data.get('osm3s',{}).get('timestamp_osm_base'),
            'bbox':BBOX,'features':features,'addresses':addresses}
    (folder/'moscow-center.json').write_text(json.dumps(result,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    print(f'Local OSM extract: {len(features)} features, {len(addresses)} addresses; bounded Moscow centre only.')


if __name__=='__main__':main()
