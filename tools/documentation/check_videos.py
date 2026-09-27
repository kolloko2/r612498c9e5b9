"""Check playable streams and create frame sheets from the final walkthroughs."""
import json,subprocess
from pathlib import Path
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'output/videos-2026-09-28';QA=ROOT/'artifacts/video-2026-09-28/qa';QA.mkdir(exist_ok=True)
for f in sorted(OUT.glob('*.mp4')):
 d=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(f)]))
 duration=float(d['format']['duration']);v=next(s for s in d['streams'] if s['codec_type']=='video');a=next(s for s in d['streams'] if s['codec_type']=='audio')
 assert (v['width'],v['height'])==(1600,1080) and a['codec_name']=='aac'
 sheet=Image.new('RGB',(1600,1080),'white')
 for i,ratio in enumerate([.06,.25,.47,.66,.83,.96]):
  frame=QA/f'{f.name[:2]}-{i}.png'
  subprocess.run(['ffmpeg','-v','error','-y','-ss',str(duration*ratio),'-i',str(f),'-frames:v','1',str(frame)],check=True)
  im=Image.open(frame);im.thumbnail((800,350));sheet.paste(im,((i%2)*800,(i//2)*360));ImageDraw.Draw(sheet).text(((i%2)*800+10,(i//2)*360+340),f'{duration*ratio:.1f} s',fill='black')
 sheet.save(QA/f'{f.name[:2]}-sheet.png')
 print(f.name,round(duration,1),'seconds',round(f.stat().st_size/1024/1024,1),'MiB')
