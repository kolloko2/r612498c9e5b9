"""Compare warmed one/two-worker Silero synthesis; no SIP/STT/backend/database."""
import argparse
import asyncio
import json
import time
from app.audio.tts import SileroPool


async def measure(model, workers, calls):
    pool = SileroPool(model, workers, 2)
    try:
        await asyncio.gather(*(pool.ensure(i) for i in range(workers)))
        await asyncio.gather(*(pool.render(f'Проверка связи, линия {i+1}.', 'baya', 1)
                               for i in range(workers)))
        start = time.perf_counter()
        async def render(i):
            audio = await pool.render(f'Бригада номер {i + 1} прибыла по адресу. Приступаем к работам.', 'baya', 1)
            return {'index': i, 'seconds': round(time.perf_counter()-start, 3), 'bytes': len(audio)}
        results = await asyncio.gather(*(render(i) for i in range(calls)))
        cached_start = time.perf_counter()
        await asyncio.gather(*(render(i) for i in range(calls)))
        return {'workers': workers, 'threads_per_worker': 2, 'calls': results,
                'total_seconds': max(r['seconds'] for r in results),
                'cached_batch_seconds': round(time.perf_counter()-cached_start, 3)}
    finally:
        await pool.close()


async def main(args):
    results = [await measure(args.model, n, args.calls) for n in (1, 2)]
    print(json.dumps(results, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--calls', type=int, choices=range(1, 21), default=5)
    asyncio.run(main(parser.parse_args()))
