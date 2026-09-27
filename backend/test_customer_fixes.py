import io
import json
import sys
from pathlib import Path

import pytest
from briefing import check
from grammar import analyze
from material_text import extract, retrieve
from server import Scenario
from territories import recipients


def test_report_rejects_negation_wrong_component_and_missing_injured():
    card = {'street':'Тверская','house':'17','incident_type':'Пожар в квартире',
            'building':'2','apartment':'5','injured':True}
    assert not check('Тверская 17 пожар не подтверждён', card)['complete']
    assert not check('Тверская 17 квартира 2 корпус 5 пожар пострадавшие есть', card)['complete']
    assert not check('Тверская 17 корпус 2 квартира 5 пожар. Пострадавших нет.', card)['complete']
    assert check('На Тверской 17 корпус 2 квартира 5 возгорание. Есть пострадавшие.', card)['complete']
    assert not check('Тверская 17 корпус 2 квартира 5 пожар. Пожара нет. Есть пострадавшие.', card)['complete']


def test_incident_not_accepted_from_incidental_noun():
    assert not check('Лесная 12 квартира', {'street':'Лесная','house':'12','incident_type':'Пожар в квартире'})['complete']
    assert not check('Лесная 120 пожар', {'street':'Лесная','house':'12','incident_type':'Пожар'})['complete']


def test_report_teacher_selects_additional_fields():
    card = {'city':'Москва','object':'Школа','incident_type':'пожар',
            '_brief_required_fields':['city','object','incident_type']}
    assert not check('В Москве пожар',card)['complete']
    assert check('В Москве пожар в школе',card)['complete']


def test_material_retrieves_late_passage_not_only_document_start():
    chunks, info = extract(('Общее введение. '*1500+'\nЗавершение работ без бригады службой 103.').encode(), 'long.txt')
    doc = {'id':'x','title':'Памятка','revision':1,'updated_at':'2026','_passages':chunks}
    result = retrieve([doc], 'Завершение работ без бригады 103', budget=1800)
    assert info['status']=='ready'
    assert 'без бригады' in result[0]['excerpt']


def test_pdf_reads_page_four_and_xlsx_retains_location():
    from reportlab.pdfgen.canvas import Canvas
    import openpyxl
    pdf = io.BytesIO(); canvas = Canvas(pdf)
    for index in range(4):
        canvas.drawString(20,700, 'Training reference page number '+str(index+1)+' with complete text')
        canvas.showPage()
    canvas.save()
    chunks, info = extract(pdf.getvalue(),'reference.pdf')
    assert info['status']=='ready' and any(c['location']=='Страница 4' for c in chunks)
    book = openpyxl.Workbook(); book.active.append(['Щукино','СЗАО']); stream=io.BytesIO();book.save(stream)
    chunks, info = extract(stream.getvalue(),'directory.xlsx')
    assert 'Щукино' in chunks[0]['text'] and 'строка 1' in chunks[0]['location']


def test_missing_ocr_is_visible(monkeypatch):
    from reportlab.pdfgen.canvas import Canvas
    monkeypatch.setenv('TESSERACT_CMD','uninstalled-test-ocr')
    pdf=io.BytesIO();c=Canvas(pdf);c.rect(20,20,100,100);c.showPage();c.save()
    chunks, info = extract(pdf.getvalue(),'scan.pdf')
    assert info['status']=='partial' and info['warnings'] and not chunks


def test_dictionary_suggestions_do_not_automatically_penalize():
    result = analyze({'description':'обнаружена задымленность и абракадабрыыыы'})
    assert result['suggestions']
    assert any(i['kind']=='dictionary' for i in result['suggestions'])
    assert not analyze({'street':'Дубининская'})['critical_errors']


def test_customer_territory_example_uses_area_for_district(monkeypatch):
    monkeypatch.delenv('TERRITORIAL_ROUTES_FILE',raising=False)
    found = recipients({'city':'Москва','area':'Щукино','object':'Школа'})
    assert {r['service'] for r in found} == {'Район Щукино','СЗАО','Департамент образования'}
    assert recipients({'city':'Москва','area':'Неизвестный район','object':'Школа'}) == []


def test_published_corrections_need_visible_source():
    base = dict(id='test-evidence',title='Учебная карточка',victim_name='Заявитель',incident='Пожар',
                location='Лесная 12',known_facts=['Дом 12'],emotion='спокойно',opening='Учебный звонок',
                dds_expectation={'expected_corrections':{'house':'14'}})
    with pytest.raises(ValueError,match='вводная'):
        Scenario.model_validate(base)
    base['dds_expectation']['correction_evidence']={'house':'Уточнение от заявителя: дом 14'}
    assert Scenario.model_validate(base).enabled


def test_caller_role_has_priority_over_casualty_name():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
    from import_tickets import _caller_name
    assert _caller_name('Иванов Иван Иванович упал. Вызывает брат.') == 'Брат'


def test_weights_change_score_but_do_not_hide_critical_errors():
    from dds_review import review
    from test_dds_review import card, event, SERVICE
    value=card([
        event(1,'service.updated','2026-09-18T10:00:00+00:00',service=SERVICE,status='Принята'),
        event(2,'situation.update','2026-09-18T10:00:00+00:00',id='departure'),
        event(3,'service.updated','2026-09-18T10:05:00+00:00',service=SERVICE,status='Начало реагирования')
    ],unlocks={'departure':'Начало реагирования'})
    expectation={'should_accept':True,'check_weights':{'acceptance':3,'update:departure':1},'pass_percent':70}
    report=review(value,expectation)
    assert report['score_percent']==75 and report['passed']
    value['events']=value['events'][1:]
    expectation['pass_percent']=0
    assert not review(value,expectation)['passed']


def test_result_keyword_cannot_be_negated():
    from dds_review import review
    from test_dds_review import card,event,SERVICE,verdict
    value=card([event(1,'service.updated','2026-09-18T10:00:00+00:00',service=SERVICE,
        status='Работы завершены',comment='Пожар не ликвидирован, работа продолжается.')])
    assert not verdict(review(value,{'result_keywords':['ликвидирован']}),'result')['passed']


def test_directory_import_keeps_unapproved_rows_inactive(tmp_path,monkeypatch):
    import openpyxl
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
    from import_territories import convert
    book=openpyxl.Workbook();sheet=book.active
    sheet.append(['id','approved','source','city','area','services'])
    sheet.append(['one','да','Согласованное учебное правило','Москва','Учебный','Служба А'])
    sheet.append(['two','нет','Черновик','Москва','Учебный','Служба Б'])
    source=tmp_path/'source.xlsx';output=tmp_path/'rules.json';book.save(source)
    assert convert(source,output)['approved']==1
    monkeypatch.setenv('TERRITORIAL_ROUTES_FILE',str(output))
    assert [v['service'] for v in recipients({'city':'Москва','area':'Учебный'})]==['Служба А']
    with pytest.raises(ValueError,match='существует'):
        convert(source,output)
