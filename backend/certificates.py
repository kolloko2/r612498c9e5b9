"""Training-only PDF certificate exports, gated by the current passing verdict."""
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
from uuid import UUID
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException

from assessment import effective, expert_history


def certificate_pdf(student, title, teacher, finished, result, identifier):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    font = Path(os.getenv('CERTIFICATE_FONT', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
    if not font.is_file():
        raise RuntimeError('Certificate font is not configured')
    if 'TrainingSans' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('TrainingSans', str(font)))
    output = io.BytesIO()
    document = SimpleDocTemplate(output, pagesize=landscape(A4),
        rightMargin=66, leftMargin=66, topMargin=60, bottomMargin=54,
        title='Сертификат выполнения учебного задания', author='Тренажёр 112')
    normal = ParagraphStyle('body', fontName='TrainingSans', fontSize=12, leading=18, alignment=TA_CENTER, textColor=colors.HexColor('#243b53'))
    heading = ParagraphStyle('heading', parent=normal, fontSize=27, leading=33)
    subheading = ParagraphStyle('subheading', parent=normal, fontSize=17, leading=23)
    small = ParagraphStyle('small', parent=normal, fontSize=9, leading=13)
    score = result.get('score_percent')
    parts = [Paragraph('ТРЕНАЖЁР 112 · УЧЕБНЫЙ КОНТУР', small), Spacer(1,18),
             Paragraph('СЕРТИФИКАТ', heading),
             Paragraph('выполнения учебного задания', subheading), Spacer(1,22),
             Paragraph(escape(str(student)), subheading), Spacer(1,10),
             Paragraph('успешно выполнил(а) задание', normal),
             Paragraph(escape(str(title)), normal), Spacer(1,18),
             Paragraph('Результат: зачтено' + (f' · {score:g}%' if isinstance(score,(int,float)) else ''), normal),
             Paragraph('Преподаватель: ' + escape(str(teacher)), normal),
             Paragraph('Задание завершено: ' + escape(str(finished)), small),
             Spacer(1,18), Paragraph('Идентификатор: '+escape(identifier),small),
             Paragraph('Сформирован: '+datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),small),
             Spacer(1,16), Paragraph('Учебный документ. Не является документом об образовании или допуском к работе в экстренных службах. Актуальный результат доступен в журнале тренажёра.',small)]
    def frame(canvas, doc):
        canvas.setStrokeColor(colors.HexColor('#1f5373'))
        canvas.setLineWidth(1)
        canvas.rect(30,30,landscape(A4)[0]-60,landscape(A4)[1]-60)
    document.build(parts,onFirstPage=frame,onLaterPages=frame)
    return output.getvalue()


def router(store, accounts, learning, authorize):
    api=APIRouter(dependencies=[Depends(authorize)])
    def export(sid,user):
        row=store.db.execute('SELECT body FROM workspace WHERE id=?',(str(sid),)).fetchone()
        value=json.loads(row[0]) if row else None
        if not value or not learning.owns_session(user,value):
            raise HTTPException(404,'Задание не найдено')
        if value.get('status')!='Завершена':
            raise HTTPException(409,'Задание ещё не завершено')
        expert=expert_history(store,str(sid))['current']
        result=effective(value,expert)
        if result.get('passed') is not True:
            raise HTTPException(409,'Сертификат доступен только при результате «Зачтено»')
        identifier=hashlib.sha256((str(sid)+json.dumps(result,sort_keys=True)).encode()).hexdigest()[:20]
        student=(accounts.get_user(value.get('student_id')) or {}).get('display_name','Студент')
        teacher=(accounts.get_user(value.get('teacher_id')) or {}).get('display_name','Не указан')
        try:
            data=certificate_pdf(student,value.get('title','Учебное задание'),teacher,value.get('finished_at',''),result,identifier)
        except (RuntimeError,ImportError):
            raise HTTPException(503,'Формирование PDF не настроено') from None
        return {'filename':f'certificate-{identifier}.pdf','content_type':'application/pdf',
                'file_base64':base64.b64encode(data).decode(),'certificate_id':identifier}

    @api.get('/api/v1/student/sessions/{sid}/certificate')
    def student_certificate(sid:UUID,user=Depends(accounts.require('student'))):
        return export(sid,user)

    @api.get('/api/v1/instructor/sessions/{sid}/certificate')
    def teacher_certificate(sid:UUID,user=Depends(accounts.require('teacher'))):
        return export(sid,user)
    return api
