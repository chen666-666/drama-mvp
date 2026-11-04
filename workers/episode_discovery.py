"""Independent manage-only metadata worker. One page per turn, short SQLite transactions."""
import json
import os
from pathlib import Path
import threading
import time
import uuid
from datetime import timedelta

import httpx
from sqlalchemy import update
from database import SessionLocal, init_db
from models import DiscoveryTask
from services import episode_discovery as service
from services.common import utcnow, iso
from sources.episode_discovery import DiscoverySource
from sources.douk import SourceUnavailable

HEARTBEAT = Path(os.getenv('DRAMA_DATA_DIR', str(Path(__file__).resolve().parents[1]/'data'))) / 'discovery-worker.json'


def writing(factory):
    session=factory()
    session.connection().exec_driver_sql('BEGIN IMMEDIATE')
    return session


def renew(factory, identifier, owner):
    with factory.begin() as session:
        now=utcnow()
        return session.execute(update(DiscoveryTask).where(DiscoveryTask.id==identifier,
            DiscoveryTask.status=='running',DiscoveryTask.owner==owner,DiscoveryTask.lease_until>now).values(
            lease_until=now+timedelta(seconds=service.LEASE_SECONDS),heartbeat_at=now)).rowcount==1


def process_turn(factory=SessionLocal, source=None, owner=None, wait=time.sleep):
    owner=owner or uuid.uuid4().hex
    source=source or DiscoverySource()
    with writing(factory) as session:
        service.recover(session)
        identifier=service.claim(session,owner)
        if not identifier:
            session.commit();return False
        request=service.reserve(session,session.get(DiscoveryTask,identifier),owner)
        session.commit()
    if not request:
        return True
    stop=threading.Event()
    lost=threading.Event()
    def keep_lease():
        while not stop.wait(10):
            try:
                if not renew(factory,identifier,owner):
                    lost.set();return
            except Exception:
                lost.set();return
    thread=threading.Thread(target=keep_lease,daemon=True)
    thread.start()
    try:
        delay=(2,5)[min(request['retry']-1,1)] if request['retry'] else 0
        if delay:
            wait(delay)
        with factory() as session:
            task=session.get(DiscoveryTask,identifier)
            can_request=service.enabled() and service.owns(task,owner) and (utcnow()-task.started_at).total_seconds()<600
        if lost.is_set() or not can_request:
            return True
        result=source.fetch(request['kind'],request['object_key'],request['cursor'])
        with writing(factory) as session:
            service.save_page(session,session.get(DiscoveryTask,identifier),owner,request,result)
            session.commit()
    except Exception as exc:
        with writing(factory) as session:
            task=session.get(DiscoveryTask,identifier)
            if service.owns(task,owner):
                # Only transient transport faults retry, consuming the same stage/total quotas.
                retryable=isinstance(exc,(httpx.TimeoutException,httpx.NetworkError)) or getattr(exc,'status',None)=='transient'
                if retryable and request['retry']<2:
                    task.status,task.owner,task.lease_until='queued','',None
                    task.message='临时网络故障，将在统一节奏下有限重试'
                    task.updated_at=utcnow()
                else:
                    service.fail(session,task,owner,exc)
                    from models import DiscoverySource as SourceProgress
                    progress=session.get(SourceProgress,request['source_id'])
                    progress.state='failed'
                    if getattr(exc,'status',None)=='unavailable' and request['kind']!='detail':
                        progress.ended,progress.state=True,'unavailable'
                        task.status='queued'
                        task.message='该来源对象不可访问，保留问题并轮转其他来源'
            session.commit()
    finally:
        stop.set();thread.join(timeout=2)
    return True


def main():
    from services.site import public_mode
    if public_mode():
        raise RuntimeError('公开展示模式不运行补集工作进程')
    init_db()
    owner=uuid.uuid4().hex
    while True:
        HEARTBEAT.parent.mkdir(parents=True,exist_ok=True)
        # Each process gets its own temporary name; atomic replacement for the status reader.
        temporary=HEARTBEAT.with_suffix('.'+owner+'.tmp')
        temporary.write_text(json.dumps({'at':iso(utcnow()),'enabled':service.enabled()}),encoding='utf-8')
        temporary.replace(HEARTBEAT)
        process_turn(owner=owner)
        time.sleep(2)


if __name__=='__main__':
    main()
