"""Assign the introductory DDS exercise through normal teacher APIs.

TRAINER_URL, TRAINER_CA, TRAINER_TEACHER, TRAINER_PASSWORD, TRAINER_STUDENT_ID
are provided by the operator. No account or existing attempt is modified.
"""
import json, os, ssl
from pathlib import Path
from seed_demo import Client

def main():
    c=Client(os.environ['TRAINER_URL'])
    c.context=ssl.create_default_context(cafile=os.environ.get('TRAINER_CA'))
    c.call('POST','/auth/login',{'username':os.environ['TRAINER_TEACHER'],'password':os.environ['TRAINER_PASSWORD']})
    scenario=json.loads((Path(__file__).parent/'data/dds_guided_practice.json').read_text(encoding='utf-8'))
    c.base_path='/api'
    existing=c.call('GET','/scenarios')[1]
    if not any(s['id']==scenario['id'] for s in existing):c.call('POST','/scenarios',scenario)
    c.base_path='/api/v1'
    uid=os.environ['TRAINER_STUDENT_ID']
    title='Первое занятие ДДС — практика с подсказками'
    groups=c.call('GET','/instructor/groups')[1]
    group=next((g for g in groups if g['title']==title and uid in g['member_ids']),None)
    if not group:
        group=c.call('POST','/instructor/groups',{'title':title})[1]
        c.call('POST',f'/instructor/groups/{group["id"]}/members',{'student_id':uid})
    lessons=c.call('GET','/instructor/lessons')[1]
    lesson=next((l for l in lessons if l['group_id']==group['id'] and l['state']=='running'),None)
    if not lesson:
        lesson=c.call('POST','/instructor/lessons',{'title':title,'group_id':group['id'],'mode':'actions',
            'prefilled_scenario_ids':[scenario['id']],'cards_per_student':1,'transport':'text'})[1]
        c.call('POST',f'/instructor/lessons/{lesson["id"]}/start',{})
    print(json.dumps({'lesson_id':lesson['id'],'title':title,'scenario_id':scenario['id']},ensure_ascii=False))

if __name__=='__main__':main()
