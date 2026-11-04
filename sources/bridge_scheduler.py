"""Single bridge instance: FIFO upstream admission, shared cooldown and account stop gate."""
import asyncio
import time
import json
from pathlib import Path


class RequestGate:
    def __init__(self, interval=2, clock=time.monotonic, sleep=asyncio.sleep, state_path=None):
        self.lock=asyncio.Lock()
        self.interval,self.clock,self.sleep=interval,clock,sleep
        self.last_start=None
        self.blocked=None
        self.state_path=Path(state_path) if state_path else None
        if self.state_path and self.state_path.exists():
            try:
                value=json.loads(self.state_path.read_text(encoding='utf-8')).get('status')
                self.blocked=value if value in ('needs_login','blocked') else None
            except (OSError,ValueError):
                self.blocked='blocked'

    def persist(self):
        if self.state_path:
            temporary=self.state_path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'status':self.blocked}),encoding='utf-8')
            temporary.replace(self.state_path)

    async def run(self, callback):
        async with self.lock:
            if self.blocked:
                return {'status':self.blocked}
            if self.last_start is not None:
                delay=self.interval-(self.clock()-self.last_start)
                if delay>0:
                    await self.sleep(delay)
            self.last_start=self.clock()
            result=await callback()
            if result.get('status') in ('needs_login','blocked'):
                self.blocked=result['status']
                self.persist()
            return result

    async def resume(self):
        async with self.lock:
            self.blocked=None
            self.persist()
