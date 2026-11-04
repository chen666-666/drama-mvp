"""Bounded read-only source verification; no database imports, media fetches or raw responses."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from sources.episode_discovery import DiscoverySource, public_metadata, REVISION
from sources.douk import DouKSource, SourceUnavailable


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--video',action='append',required=True)
    parser.add_argument('--keyword',default='AI短剧')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    if len(args.video)>3 or any(not v.isascii() or not v.isdigit() for v in args.video):
        raise SystemExit('Provide up to three public numeric video IDs')
    report={'revision':REVISION,'health':DouKSource().health(),'requests':[],
            'input_video_ids':args.video,'search_keyword':args.keyword,
            'business_database_writes':0,'media_downloads':0,'verified_multpage':False,
            'scope':'最多8次公开元数据只读请求；未代替同剧正片或公开范围人工核验'}
    adapter=DiscoverySource();blocked=False
    def fetch(kind,key,cursor):
        nonlocal blocked
        if blocked or len(report['requests'])>=8:
            return None
        started=time.monotonic()
        entry={'kind':kind,'cursor':str(cursor),'publish_time':0 if kind=='search' else None}
        try:
            result=adapter.fetch(kind,key,str(cursor))
            metas=[public_metadata(v) for v in result['videos']]
            entry.update(status='ok',count=len(metas),next_cursor=result['cursor'],has_more=result['has_more'],
                authors=[{'uid':m['uid'],'sec_uid':m['sec_uid']} for m in metas[:3]],
                video_ids=[m['video_id'] for m in metas])
            return result
        except SourceUnavailable as exc:
            entry.update(status=exc.status)
            blocked=exc.status in ('needs_login','blocked')
            return None
        except Exception:
            entry.update(status='failed')
            return None
        finally:
            entry['elapsed_seconds']=round(time.monotonic()-started,3)
            report['requests'].append(entry)
    report['supported_revision']=report['health'].get('revision')==REVISION
    if report['health'].get('available') and report['supported_revision']:
        for vid in args.video:
            detail=fetch('detail',vid,0)
            if not detail:
                continue
            meta=public_metadata(detail['videos'][0])
            for kind,key in [('mix',meta['mix_id']),('author',meta['sec_uid'])]:
                if not key:continue
                first=fetch(kind,key,0)
                if first and first['has_more']:
                    second=fetch(kind,key,first['cursor'])
                    if second:report['verified_multpage']=True
            fetch('search',args.keyword,0)
    report['ready_for_enable']=report['verified_multpage'] and all(r['status']=='ok' for r in report['requests'])
    report['limitation']='此探测不证明全剧完整，也不证明混发作品匹配准确；启用前须核对三类样本及人工范围'
    path=Path(args.output).resolve();path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'requests':len(report['requests']),'statuses':[r['status'] for r in report['requests']],
        'verified_multpage':report['verified_multpage'],'report':str(path)},ensure_ascii=False))


if __name__=='__main__':main()
