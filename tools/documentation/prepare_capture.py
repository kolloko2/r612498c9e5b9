"""Synthetic lesson for screenshots only. Does not alter deployed configuration."""
import json
import sys
from pathlib import Path
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from acceptance_dds_sip import client
teacher, student=client('prepod'),client('kursant1')
call=lambda c,m,p,b=None:c.call(m,p,b)[1]
me=call(student,'GET','/auth/me')
item=call(teacher,'GET','/instructor/tickets/1')['calls'][2]
group=call(teacher,'POST','/instructor/groups',{'title':'Руководство пользователя '+uuid4().hex[:4]})
call(teacher,'POST',f'/instructor/groups/{group["id"]}/members',{'student_id':me['id']})
lesson=call(teacher,'POST','/instructor/lessons',{'title':'Работа ДДС с входящей карточкой','group_id':group['id'],'mode':'actions','prefilled_scenario_ids':[item['published_scenario_id']],'cards_per_student':1,'transport':'text'})
call(teacher,'POST',f'/instructor/lessons/{lesson["id"]}/start',{})
session=call(student,'POST',f'/student/lessons/{lesson["id"]}/next',{})
call(student,'POST',f'/student/sessions/{session["id"]}/services',{'service':session['owner_service'],'status':'Принята','comment':'Принято в работу','message_id':str(uuid4())})
out=Path(__file__).resolve().parents[2]/'artifacts/documentation-2026-09-27/session.json'
out.write_text(json.dumps({'session_id':session['id'],'lesson_id':lesson['id']}),encoding='utf-8')
print('Synthetic documentation lesson prepared')
