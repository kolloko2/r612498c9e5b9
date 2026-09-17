"""Stage real local speech and localhost TLS, keeping the topology safety gate."""
from pathlib import Path
import os
import tempfile

ROOT=Path(__file__).resolve().parents[1]
VALUES={'INSTALL_LOCAL_PROVIDERS':'true','INSTALL_SILERO':'true',
        'STT_PROVIDER':'vosk','STT_MODEL':'/models/vosk-model-small-ru-0.22',
        'TTS_PROVIDER':'silero','TTS_VOICE':'/models/v5_5_ru.pt','TTS_SPEAKER':'baya',
        'PIPELINE_MODE':'conversation','TLS_ALLOWED_ORIGINS':'https://127.0.0.1:3000,https://localhost:3000'}


if __name__=='__main__':
    target=ROOT/'.env.docker'
    lines=target.read_text(encoding='utf-8').splitlines()
    lines=[line for line in lines if line.split('=',1)[0].strip() not in VALUES]
    lines += [key+"='"+value+"'" for key,value in VALUES.items()]
    fd,name=tempfile.mkstemp(dir=target.parent,prefix='.speech-tls-',suffix='.tmp')
    with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as stream:
        stream.write('\n'.join(lines)+'\n');stream.flush();os.fsync(stream.fileno())
    os.replace(name,target)
    print('Speech and TLS environment staged. Topology gate left unchanged.')
