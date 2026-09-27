"""Read installed package versions; never read ENV or container inspection data."""
import json,subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[2]
out=root/'output/documentation-2026-09-27'
packages={}
for service in ('backend','frontend','voice'):
    raw=subprocess.check_output(['docker','exec',f'trainer112-{service}-1','python','-m','pip','list','--format=json'],encoding='utf8')
    packages[service]=json.loads(raw)
inventory={'date':'2026-09-27','commit':'9fb10f8eee0402f3fd09fc63f0a7b7126d1f616d','profile':'local CPU; no external AI provider','python_packages':packages}
(out/'technology_inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf8')
measurements={'date':'2026-09-27','cpu':'AMD Ryzen 5 5600','ram_gb':32,'gpu_used':False,'model':'qwen3:4b-instruct-2507-q4_K_M','generation_ms':[3470,6004,6370,8283,9239],'output_tokens':[36,45,48,64,61],'turn_latency_ms':[5022,7118,7331,9559,10731],'tests':{'backend_passed':355,'backend_skipped':3,'voice_passed':56},'source':'docs/CPU_PHONE_2026-09-27.md','scope':'Single full lesson; not a concurrency certification','assessment':{'percent':91.67,'checks_passed':11,'checks_total':12,'duration_seconds':355,'limit_seconds':350,'script_overall_passed':False}}
(out/'measurements.json').write_text(json.dumps(measurements,ensure_ascii=False,indent=2),encoding='utf8')
print('Version inventory and measurements saved')
