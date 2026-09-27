"""Finish only the synthetic lesson created for the documentation illustrations."""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from acceptance_dds_sip import client
root=Path(__file__).resolve().parents[2]
e=json.loads((root/'artifacts/documentation-2026-09-27/session.json').read_text())
status,_=client('prepod').call('POST',f'/instructor/lessons/{e["lesson_id"]}/finish',{'reason':'Подготовка иллюстраций руководства завершена'})
print('Documentation lesson finish status:',status)
