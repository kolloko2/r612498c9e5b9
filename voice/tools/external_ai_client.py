"""Server-side example. Set VOICE_API_TOKEN; pip install httpx."""
import asyncio
import os
from uuid import uuid4
import httpx

class VoiceClient:
    def __init__(self):
        self.http = httpx.AsyncClient(base_url=os.getenv('VOICE_API_URL', 'http://127.0.0.1:8001') + '/api/v1/integration',
             headers={'Authorization': 'Bearer ' + os.environ['VOICE_API_TOKEN']}, timeout=65, trust_env=False)

    async def request(self, method, path, **kwargs):
        r = await self.http.request(method, path, **kwargs)
        r.raise_for_status()
        return r.json()

    async def speak(self, cid, text, message_id=None):
        # For a network retry pass the SAME message_id; don't generate a new one.
        return await self.request('POST', f'/calls/{cid}/speak', json={
            'message_id': message_id or str(uuid4()), 'text': text})

async def on_operator(text):
    print('Оператор:', text)
    # Replace with your AI request. Return its victim-role answer as a string.
    # None means only read the transcript, without speaking a reply.
    return None

async def main():
    voice = VoiceClient()
    cid = None
    try:
        call = await voice.request('POST', '/calls', json={'session_id': str(uuid4()), 'extension': '201'})
        cid = call['call_id']
        print('Примите звонок в MicroSIP. call_id:', cid)
        async with asyncio.timeout(60):
            while True:
                state = await voice.request('GET', f'/calls/{cid}')
                if state['status'] == 'active': break
                if state['status'] in ('ended', 'failed'): return
                await asyncio.sleep(.3)
        await voice.speak(cid, 'Помогите, у меня в квартире пожар!')
        cursor = '0'
        while True:
            batch = await voice.request('GET', f'/calls/{cid}/events', params={'follow': 'false', 'after': cursor})
            for event in batch['events']:
                if event['type'] == 'operator.final':
                    reply = await on_operator(event['payload']['text'])
                    if reply: await voice.speak(cid, reply)
                cursor = event['event_id']
                if event['type'] == 'call.updated' and event['payload'].get('status') in ('ended', 'failed'):
                    return
            await asyncio.sleep(.25)
    finally:
        if cid:
            try: await voice.request('POST', f'/calls/{cid}/hangup')
            except httpx.HTTPError: pass
        await voice.http.aclose()

if __name__ == '__main__':
    asyncio.run(main())
