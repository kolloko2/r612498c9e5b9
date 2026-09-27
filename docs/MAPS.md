# Local MapLibre map

The map viewer uses MapLibre GL JS 5.18.0, PMTiles 4.5.0 and the Protomaps
5.7.2 light style with Russian labels. All scripts, worker code, fonts, sprites
and vector tiles come from the trainer server. No runtime CDN, API key, external
geocoder or new microservice is required. Client rendering uses browser WebGL2;
this does not change the CPU-only inference configuration on the server.

## Installation

Browser assets are checked in under `frontend/assets/maplibre`; rebuild them on
a connected machine with `python tools/prepare_maplibre.py`. Provenance, versions
and third-party licenses are in that directory.

Prepare the map archive on a connected machine using the official go-pmtiles CLI
1.31.2 (https://github.com/protomaps/go-pmtiles/releases):

```text
pmtiles extract https://build.protomaps.com/20260927.pmtiles deploy/maps/region.pmtiles --bbox=35.1,54.2,40.3,56.95 --maxzoom=15 --download-threads=4
pmtiles verify deploy/maps/region.pmtiles
```

The prepared archive is approximately 584 MB, SHA256
`8460590bc202a4334c225119b616c4a574074884d7c6ce927b18834c2974350c`.
The bounding rectangle covers Moscow and Moscow Oblast, not their administrative
boundary. Source zooms 0–15 are available; the viewer magnifies detailed tiles up
to zoom 19. Panning is bounded to the installed rectangle.

Keep `deploy/maps/regional.sqlite` for address search. To prepare that index:

```text
python -m pip install "osmium>=4,<5"
python tools/prepare_regional_map.py --download
```

Copy both files to `deploy/maps` on the target server. Compose mounts the folder
read-only into Backend (search) and Frontend (map archive). Large map packages
are excluded from Git and Docker images. The frontend exposes only the fixed
`/map-data/region.pmtiles` resource and supports HTTP Range, so browsers download
the visible tiles rather than the entire archive. Missing files produce 503 and
a visible retry action. SQLite and source extracts are not publicly served.

The old `/api/v1/student/map/features` endpoint remains for compatibility but is
not used by this viewer. Its simplified geometry is not used as a fallback.
The vector package supplies proper water polygons, buildings, road hierarchy,
bridges, parks and collision-managed labels.

## Interaction

«Указать на карте» follows the supplied card-entry instruction (figures 20–21).
Click selects WGS84 coordinates; dragging only pans. Arrow keys pan when the map
canvas has focus; Enter selects the centre. Buttons copy coordinates and return
to the original point. Editable 112 cards require explicit transfer confirmation
and saving; DDS/completed cards only allow copying. Search selects the same marker.
Selecting a point does not automatically change the address text.

Copyright/ODbL and Protomaps attribution remains visible. The renderer uses a
same-origin CSP worker; external network connections are blocked by page CSP.

## Verification

```text
python -m pytest frontend/test_map_archive.py frontend/test_proxy.py frontend/test_tls.py backend/test_maps.py -q
node frontend/test_map_browser.cjs
node frontend/test_arm_browser.cjs
```

The map browser test reads the actual PMTiles archive, forbids external requests,
and checks search, selection, drag, zoom, coordinate transfer and DDS restrictions.
