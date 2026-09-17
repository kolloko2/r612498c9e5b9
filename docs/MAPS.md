# Offline regional map

The former centre-only JSON is superseded by the whole Moscow and Moscow Oblast
OSM extracts from https://download.openstreetmap.fr/extracts/russia/central_federal_district/.
The map is no longer hard-coded to a small bounding box. The viewport reads a
spatial index on Backend; address search uses SQLite FTS5. No new map microservice,
database server, Internet access or API key is required at runtime.

Prepare on a connected machine:

```
python -m pip install "osmium>=4,<5"
python tools/prepare_regional_map.py --download
```

Copy `deploy/maps/regional.sqlite` alongside the Compose deployment and models.
It mounts read-only into Backend at `/data/maps`. The large public package and
source PBFs are excluded from Git, build contexts and private-data backups.
Both input files cover their provider's complete regions, including New Moscow;
this is not a claim that every real-world address exists in OSM. Additional regions
can be prepared using the same schema without changing runtime code.

The import preserves nodes/ways with street/house addresses, roads, building ways,
park/land-use ways and water ways. Complex multipolygon relations are not currently
assembled. Addresses lacking a city tag retain the source region label rather than
inventing a municipality. Export manifests retain source SHA256 and preparation time.

The viewer supports dragging, arrow-key navigation, zoom buttons/wheel, searching
before a card has coordinates, and explicitly sending selected coordinates back
to the student card. Large views omit fine detail by min_zoom and cap 8,000 features;
zooming in reveals the detailed objects. Source geometry is not rewritten by display
simplification. Copyright/ODbL attribution remains visible as required by the source.

Checks: `python -m pytest backend/test_maps.py -q` and
`node frontend/test_map_math.cjs`.
