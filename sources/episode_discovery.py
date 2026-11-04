"""Strict, public metadata only. Discovery never uses the ranking importer."""
import re
import unicodedata

from sources.download import bridge_request
from sources.douk import SourceUnavailable

REVISION = "e327b9549d284a8b1613566f949fe2dbb8a6c2a1"


def text(value, limit=2000):
    value = str(value or "")[:limit]
    # Keep titles as public text but never persist credential-bearing fragments or links.
    value = re.sub(r'https?://\S+', '[链接已省略]', value)
    return re.sub(r'(?i)(cookie|authorization|sessionid|mstoken|signature|x-bogus|api[_-]?key)\s*[:=]\s*\S+', '[已去敏]', value)


def normalized(value):
    return re.sub(r'[\W_]', '', unicodedata.normalize('NFKC', value)).casefold()


def number_hint(title):
    # Ranges and upper/lower halves need human interpretation, never a single number.
    if re.search(r'上[集部篇]|下[集部篇]|[（(][上下][）)]|[0-9一二三四五六七八九十百]+\s*[-—~～至到]\s*[0-9一二三四五六七八九十百]+', title):
        return None
    matches = re.findall(r'第\s*([0-9一二三四五六七八九十百两]+)\s*[集话]', title)
    if len(matches) != 1:
        return None
    raw = matches[0]
    if raw.isascii() and raw.isdigit():
        return int(raw) if 0 < int(raw) <= 10000 else None
    digits = dict(zip('一二三四五六七八九两', (1,2,3,4,5,6,7,8,9,2)))
    if len(raw)>1 and not any(c in raw for c in '十百'):
        return None
    if not re.fullmatch(r'[一二三四五六七八九两]?[百]?[一二三四五六七八九两]?[十]?[一二三四五六七八九两]?', raw):
        return None
    total, current = 0, 0
    for char in raw:
        if char in digits:
            current = digits[char]
        else:
            total += (current or 1) * {'十':10, '百':100}[char]
            current = 0
    return total + current or None


def public_metadata(raw):
    if not isinstance(raw, dict) or not re.fullmatch(r'[0-9]{1,100}', str(raw.get('aweme_id', ''))):
        raise SourceUnavailable('parse_error', '视频结构变化，未推进分页位置')
    author, mix = raw.get('author') or {}, raw.get('mix_info') or {}
    if not isinstance(author, dict) or not isinstance(mix, dict):
        raise SourceUnavailable('parse_error', '作者或合集结构变化，未推进分页位置')
    title = text(raw.get('desc'))
    if not title:
        raise SourceUnavailable('parse_error', '视频缺少标题，未推进分页位置')
    vid = str(raw['aweme_id'])
    published=raw.get('create_time')
    published=published if type(published) is int and published>=0 else None
    duration=raw.get('duration')
    duration=duration/1000 if type(duration) in (int,float) and 0<duration<86400000 else None
    return {'video_id': vid, 'title': title, 'uid': text(author.get('uid'),255),
            'sec_uid': text(author.get('sec_uid'),255), 'mix_id': text(mix.get('mix_id'),100),
            'url': f'https://www.douyin.com/video/{vid}', 'number': number_hint(title),
            'published_timestamp':published,'duration':duration,
            'content': 'compilation' if re.search(r'合集|全集|[0-9一二三四五六七八九十百]+\s*[-—~～至到]\s*[0-9一二三四五六七八九十百]+', title)
                       else 'promotional' if re.search(r'预告|花絮|解说|抢先|抢鲜', title) else 'candidate'}


def validate_page(result, cursor, key='cursor'):
    if (not isinstance(result, dict) or not isinstance(result.get('videos'), list)
        or type(result.get('has_more')) is not bool or type(result.get(key)) is not int
        or result[key] < 0):
        raise SourceUnavailable('parse_error', '分页响应缺少必要字段，不能确认末页')
    if result['has_more'] and (str(result[key]) == str(cursor) or not result['videos']):
        raise SourceUnavailable('parse_error', '分页没有推进或返回异常空页，不能确认末页')
    return result


class DiscoverySource:
    def fetch(self, kind, object_key, cursor):
        if kind == 'detail':
            result = bridge_request('/detail', {'video_id': object_key})
            return {'videos': [result.get('video')], 'cursor': 0, 'has_more': False}
        if kind == 'search':
            result = bridge_request('/search', {'keyword':object_key, 'offset':int(cursor),
                                    'sort_type':0, 'count':10, 'publish_time':0})
            validate_page(result, cursor, 'offset')
            return {**result, 'cursor':result['offset']}
        return validate_page(bridge_request('/directory', {'mix_id':object_key if kind == 'mix' else '',
            'account':object_key if kind == 'author' else '', 'cursor':int(cursor)}), cursor)
