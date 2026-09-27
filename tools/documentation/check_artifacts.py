"""Check authored artifacts without reading deployment secrets."""
from pathlib import Path
import re
import zipfile
from lxml import etree

root=Path(__file__).resolve().parents[2]/'output/documentation-2026-09-28'
pattern=re.compile(r'sk-or-v1-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{25,}|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY')
ns={'wp':'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'}
files=list(root.glob('*.docx'))
assert len(files)==12
for path in files:
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        text=archive.read('word/document.xml').decode()
        assert not pattern.search(text),path.name
        document=etree.fromstring(text.encode())
        ids=document.xpath('//wp:docPr/@id',namespaces=ns)
        assert len(ids)==len(set(ids)),path.name
        assert 'Лист регистрации изменений' not in text,path.name
print('DOCX integrity, unique drawing identifiers and secret-pattern check: 12 OK')
