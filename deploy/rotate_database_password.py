"""Rotate the local deployment DB credential without printing or passing it in argv."""
import os
from pathlib import Path
import secrets
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    target=ROOT/'.env.docker';original=target.read_text(encoding='utf-8')
    if sum(line.startswith('POSTGRES_PASSWORD=') for line in original.splitlines())!=1:
        raise RuntimeError('Expected one PostgreSQL credential entry')
    password=secrets.token_hex(32)
    program="import os,sys,psycopg; from psycopg import sql; p=sys.stdin.read().strip(); c=psycopg.connect(os.environ['DATABASE_URL'],autocommit=True); c.execute(sql.SQL('ALTER ROLE trainer PASSWORD {}').format(sql.Literal(p))); c.close()"
    command=['docker','compose','--env-file',str(target),'-f',str(ROOT/'docker-compose.yml'),
             'exec','-T','backend','python','-c',program]
    result=subprocess.run(command,input=password,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=ROOT)
    if result.returncode:raise RuntimeError('Database credential rotation failed; configuration unchanged')
    content='\n'.join("POSTGRES_PASSWORD='"+password+"'" if line.startswith('POSTGRES_PASSWORD=') else line for line in original.splitlines())+'\n'
    fd,name=tempfile.mkstemp(dir=target.parent,prefix='.rotation-',suffix='.tmp')
    with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as stream:
        stream.write(content);stream.flush();os.fsync(stream.fileno())
    os.replace(name,target)
    print('PostgreSQL role and private deployment ENV credential rotated. Restart dependent services.')


if __name__=='__main__':main()
