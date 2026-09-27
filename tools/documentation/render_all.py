"""Inside the isolated Linux renderer; no access to application credentials."""
import json,subprocess,sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
root=Path('/work')
manifest=json.loads((root/'output/documentation-2026-09-28/manifest.json').read_text())
if len(sys.argv)>1:manifest=[item for item in manifest if item['file'][:2] in sys.argv[1:]]
def render(item):
    idx=item['file'][:2]
    subprocess.run(['python','/renderer/render_docx.py',str(root/'output/documentation-2026-09-28'/item['file']),
                    '--output_dir',str(root/'artifacts/documentation-2026-09-28/render'/idx),'--emit_pdf','--dpi','110'],check=True)
with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(render,manifest))
