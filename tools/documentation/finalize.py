"""Resolve page references and package only reviewed public documentation."""
import argparse, hashlib, html, json, re, shutil, zipfile
from pathlib import Path
from pypdf import PdfReader
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'output/documentation-2026-09-28'
RENDER=ROOT/'artifacts/documentation-2026-09-28/render'
p=argparse.ArgumentParser();p.add_argument('--toc',action='store_true');p.add_argument('--package',action='store_true');args=p.parse_args()
manifest=json.loads((OUT/'manifest.json').read_text(encoding='utf8'))
normalize=lambda text:re.sub(r'\s+','',text)
toc={}; counts={}; stale=[]
for item in manifest:
    pdf=RENDER/item['file'][:2]/Path(item['file']).with_suffix('.pdf').name
    pages=PdfReader(pdf).pages;counts[item['file']]=len(pages)
    texts=[normalize(page.extract_text()) for page in pages]
    mapping={}
    for heading in item['headings']:
        found=[i+1 for i,t in enumerate(texts) if normalize(heading) in t]
        if not found:raise RuntimeError(f'Missing heading: {item["file"]}: {heading}')
        mapping[heading]=found[-1]
    toc[item['source']]=mapping
    printed={}
    for page in pages:
        for line in page.extract_text().splitlines():
            match=re.match(r'^(.*?)\.{2,}\s*(\d+)\s*$',line)
            if match:printed[normalize(match[1])]=int(match[2])
    if any(printed.get(normalize(h))!=n for h,n in mapping.items()):stale.append(item['file'][:2])
if args.toc:
    before=json.loads((OUT/'toc.json').read_text(encoding='utf8')) if (OUT/'toc.json').exists() else {}
    (OUT/'toc.json').write_text(json.dumps(toc,ensure_ascii=False,indent=2),encoding='utf8')
    print('TOC stable:',before==toc)
    print('PDF contents to refresh:',stale)
if args.package:
    expected=json.loads((OUT/'toc.json').read_text(encoding='utf8'))
    if expected!=toc or stale:raise RuntimeError(f'Rebuild with stable page numbers before packaging: {stale}')
    for item in manifest:
        pdf=Path(item['file']).with_suffix('.pdf').name
        shutil.copy2(RENDER/item['file'][:2]/pdf,OUT/pdf)
    shutil.copy2(ROOT/'docs/delivery/sources.md',OUT/'sources.md')
    rows=[]
    for item in manifest:
        doc=item['file'];pdf=Path(doc).with_suffix('.pdf').name
        rows.append(f'<tr><td>{html.escape(item["title"])}</td><td><a href="{pdf}">PDF</a></td><td><a href="{doc}">DOCX</a></td><td>{counts[doc]}</td></tr>')
    page='''<!doctype html><html lang="ru"><meta charset="utf-8"><title>112 AI Trainer — документация</title>
    <style>body{font:17px Georgia,serif;max-width:1000px;margin:48px auto;color:#202830;background:#fff;padding:0 24px}h1{font-size:28px}table{border-collapse:collapse;width:100%}td,th{padding:13px;border-bottom:1px solid #ccd1d6;text-align:left}a{color:#16496a}p{line-height:1.6}</style>
    <h1>112 AI Trainer</h1><p>Эксплуатационная и сопроводительная документация. Версия программы v1.0. Редакция 28 сентября 2026 года.</p>
    <p>Начните с руководства для жюри. Для установки откройте руководство сервера, затем инструкцию рабочего места. Пример адреса сервера в документах — 192.168.10.10; замените его адресом своей площадки.</p>
    <table><tr><th>Документ</th><th>Чтение</th><th>Редактирование</th><th>Страниц</th></tr>'''+''.join(rows)+'''</table>
    <p>Дополнения: <a href="technology_inventory.json">версии библиотек</a>, <a href="measurements.json">измерения</a>, <a href="sources.md">источники</a>, <a href="SHA256SUMS.txt">контрольные суммы</a>.</p>
    <p>Разработчик и ответственный за документацию — Кодзаев Николай Петрович. Дата выпуска — 28.09.2026.</p></html>'''
    (OUT/'index.html').write_text(page,encoding='utf8')
    included=[f for f in OUT.iterdir() if f.suffix in ('.docx','.pdf') or f.name in ('index.html','sources.md','measurements.json','technology_inventory.json')]
    sums='\n'.join(hashlib.sha256(f.read_bytes()).hexdigest()+'  '+f.name for f in sorted(included))+'\n'
    (OUT/'SHA256SUMS.txt').write_text(sums,encoding='utf8');included.append(OUT/'SHA256SUMS.txt')
    target=ROOT/'output/112_AI_Trainer_Документация_2026-09-28.zip'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
        for f in included:archive.write(f,f.name)
    print('Packaged documents:',len(manifest),'pages:',sum(counts.values()),'files:',len(included))
    print(target)
else:print(json.dumps(counts,ensure_ascii=False))
