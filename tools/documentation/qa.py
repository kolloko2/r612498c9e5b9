"""Document-only QA: page geometry, extracted text, and visual review sheets."""
import json,re
from pathlib import Path
import fitz
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[2]
R=ROOT/'artifacts/documentation-2026-09-28/render'
QA=ROOT/'artifacts/documentation-2026-09-28/qa';QA.mkdir(exist_ok=True)
issues=[];stats=[]
for folder in sorted(p for p in R.iterdir() if p.is_dir()):
    pdf=next(folder.glob('*.pdf'));doc=fitz.open(pdf)
    pages=[]
    for n,page in enumerate(doc,1):
        text=page.get_text()
        if '\ufffd' in text:issues.append([folder.name,n,'replacement character'])
        for b in page.get_text('dict')['blocks']:
            if b['type']!=0:continue
            for line in b['lines']:
                for span in line['spans']:
                    x0,y0,x1,y1=span['bbox']
                    if x0<25 or x1>page.rect.width-20 or y0<0 or y1>page.rect.height:
                        issues.append([folder.name,n,'out of safe page bounds',span['text'][:60]])
        pages.append(folder/f'page-{n}.png')
    stats.append({'document':folder.name,'pages':len(doc)})
    for start in range(0,len(pages),4):
        sheet=Image.new('RGB',(1120,1610),'#c3c3c3');draw=ImageDraw.Draw(sheet)
        for j,path in enumerate(pages[start:start+4]):
            im=Image.open(path);im.thumbnail((550,775))
            x=(j%2)*560;y=(j//2)*805
            sheet.paste(im,(x,y+24));draw.text((x+6,y+5),f'{folder.name} / page {start+j+1}',fill='black')
        sheet.save(QA/f'{folder.name}-{start//4+1}.png')
(QA/'report.json').write_text(json.dumps({'documents':stats,'issues':issues},ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'documents':len(stats),'pages':sum(s['pages'] for s in stats),'issues':issues},ensure_ascii=False))
