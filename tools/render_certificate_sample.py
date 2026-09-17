"""Render a synthetic PDF sample, never a real qualification certificate."""
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from certificates import certificate_pdf

target=ROOT/'output/pdf/certificate-sample.pdf'
target.parent.mkdir(parents=True,exist_ok=True)
target.write_bytes(certificate_pdf('Иванов Иван Иванович','Учебное происшествие: уточнение адреса и оповещение профильных служб','Преподаватель учебной группы','2026-09-15 18:00 UTC',{'passed':True,'score_percent':95},'DEMO-NOT-A-REAL-AWARD'))
print(target)
