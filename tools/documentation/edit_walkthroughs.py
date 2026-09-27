"""Edit real Docker Chromium recordings; telephone audio is from the actual SIP calls."""
import json, subprocess, shutil, wave
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SRC=ROOT/'artifacts/video-2026-09-28'
TMP=SRC/'edit'; TMP.mkdir(exist_ok=True)
OUT=ROOT/'output/videos-2026-09-28'; OUT.mkdir(parents=True,exist_ok=True)
shutil.copy2('C:/Windows/Fonts/segoeui.ttf',TMP/'font.ttf')
shutil.copy2('C:/Windows/Fonts/seguisym.ttf',TMP/'symbols.ttf')
(TMP/'arrow.txt').write_text('➜',encoding='utf8')
def read(n):return json.loads((SRC/(n+'.json')).read_text(encoding='utf8'))
def run(args):subprocess.run(args,cwd=TMP,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
def segment(src,start,duration,title,audio=None,zoom=False,arrow=None):
    return dict(src=src,start=start,duration=duration,title=title,audio=audio,zoom=zoom,arrow=arrow)

teacher=[]
marks=read('02-teacher')['marks']
for i,m in enumerate(marks[:-1]):
    teacher.append(segment('02-teacher',m['t'],min(marks[i+1]['t']-m['t'],12),m['title'] if i else 'Кабинет преподавателя · группы, задания, результаты'))
report=read('02-teacher-results')['marks']; rs=report[1]['t']
teacher.append(segment('02-teacher-results',rs,13,'Результаты · история карточки и оценка решений'))
teacher.append(segment('02-teacher-results',rs+13,7,'Разбор ошибок · 11 из 12 критериев выполнены',zoom=True,arrow=(270,490)))

student=[segment('03-student',2.6,5,'Кабинет ученика · вход в назначенное занятие'),
         segment('03-student',14.69,4,'Входящая карточка · подтверждение получения за 30 секунд'),
         segment('03-student',66.04,1.5,'Выбор бригады · организация реагирования')]
evidence=read('evidence'); epoch=read('03-student')['started']/1000
labels=['Доклад дежурному · передача адреса и обстоятельств','Доклад бригады · начало реагирования','Доклад бригады · прибытие','Доклад бригады · проведение работ','Доклад бригады · результат работ']
status_times=[112.759,223.305,283.117,353.059]
for i,row in enumerate(evidence['calls']):
    t=datetime.fromisoformat(row['chat']['created_at']).timestamp()-epoch
    # Retain the real greeting, operator question and model reply, removing only idle tail.
    duration=[44,41,56,48,39][i]
    student.append(segment('03-student',t,duration,labels[i],audio=i))
    if i<4:student.append(segment('03-student',status_times[i],3.1,'Фиксация телефонограммы' if i==0 else 'Статус и комментарий по полученному докладу',arrow=None if i==0 else (180,741)))
sm=read('03-student')['marks']
finish=next(m['t'] for m in sm if m['title']=='Итоговый доклад · результат работ')
student.append(segment('03-student',finish,8,'Результат записан · завершение работ и карточки'))
student.append(segment('03-student',finish+8,8,'Отчёт ученика · действия, ошибки и история'))

overview=[]
om=read('01-overview')['marks']
for i,m in enumerate(om[1:-1],1):
    overview.append(segment('01-overview',m['t'],min(om[i+1]['t']-m['t'],7),m['title']))
overview.insert(3,segment('03-student',16.25,2,'АРМ ДДС · готовая входящая карточка'))
overview.insert(4,segment('03-student',finish,7,'Рабочий цикл · бригада, доклады, статусы, результат'))
overview.append(segment('02-teacher-results',rs+13,7,'Обратная связь · оценка решений диспетчера',zoom=True))

manifest=[]
for filename,segs in [('01_Обзор_решения',overview),('02_Кабинет_преподавателя',teacher),('03_Занятие_ученика_с_телефоном',student)]:
    chunks=[]; timeline=[]; elapsed=0
    for i,s in enumerate(segs):
        tag=f'{filename[:2]}_{i:02d}'; text=TMP/(tag+'.txt');text.write_text(s['title'],encoding='utf8')
        target=TMP/(tag+'.mp4')
        cmd=['ffmpeg','-hide_banner','-loglevel','error','-y','-ss',str(s['start']),'-i',str(SRC/(s['src']+'.webm'))]
        if s['audio'] is None:cmd+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
        else:cmd+=['-i',str(SRC/f"call-{s['audio']}-mixed.wav")]
        vf='fps=25,setsar=1'
        if s['zoom']:vf+=',crop=1280:800:200:180,scale=1600:1000'
        if s['arrow']:vf+=f",drawtext=fontfile=symbols.ttf:textfile=arrow.txt:fontsize=58:fontcolor=0xE0A64B:x={s['arrow'][0]}:y={s['arrow'][1]}"
        vf+=f",pad=1600:1080:0:80:color=0x17232D,drawbox=x=25:y=23:w=5:h=35:color=0xE0A64B:t=fill,drawtext=fontfile=font.ttf:textfile={tag}.txt:fontsize=26:fontcolor=white:x=48:y=23"
        cmd+=['-t',str(s['duration']),'-vf',vf,'-af','apad,alimiter=limit=0.95','-map','0:v:0','-map','1:a:0','-c:v','libx264','-preset','veryfast','-crf','21','-threads','2','-filter_threads','1','-pix_fmt','yuv420p','-c:a','aac','-b:a','160k','-ar','48000','-ac','2',str(target)]
        try:run(cmd)
        except subprocess.CalledProcessError as exc:raise RuntimeError(exc.stderr.decode(errors='replace'))
        chunks.append(target);timeline.append({'at':round(elapsed,2),'title':s['title'],'source':s});elapsed+=s['duration']
        print(filename, i+1,'/',len(segs),flush=True)
    listing=TMP/(filename[:2]+'.concat');listing.write_text('\n'.join("file '"+c.name+"'" for c in chunks),encoding='utf8')
    dest=OUT/(filename+'.mp4')
    run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',listing.name,'-c','copy','-movflags','+faststart',str(dest)])
    manifest.append({'file':dest.name,'duration':round(elapsed,2),'chapters':timeline})
(SRC/'edit-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
print('Finished:',OUT)
