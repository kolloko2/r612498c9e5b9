"""Mechanically synchronize student schemas/routes from FastAPI into shared OpenAPI."""
import os
import sys
from pathlib import Path
import yaml

root = Path(__file__).resolve().parents[1]
os.environ['DIALOGUE_DB'] = ':memory:'
os.environ['LLM_PROVIDER'] = 'mock'
sys.path.insert(0, str(root / 'backend'))
from server import app

path = root / 'shared' / 'api' / 'openapi.yaml'
contract = yaml.safe_load(path.read_text(encoding='utf-8'))
generated = app.openapi()
for route, spec in generated['paths'].items():
    if route.startswith(('/api/v1/student/', '/api/v1/instructor/', '/api/v1/auth/', '/api/v1/admin/', '/api/v1/scenarios')):
        for method in spec.values():
            if isinstance(method, dict) and 'responses' in method:
                method['security'] = [{'bearerAuth': []}]
                method['parameters'] = [p for p in method.get('parameters', []) if p.get('name') != 'authorization']
        contract['paths'][route.removeprefix('/api/v1')] = spec
for name, schema in generated['components']['schemas'].items():
    contract['components']['schemas'][name] = schema
contract['openapi'] = '3.1.0'
contract['info']['version'] = '0.3.0'
path.write_text(yaml.safe_dump(contract, allow_unicode=True, sort_keys=False), encoding='utf-8')
print('Student OpenAPI synchronized')
