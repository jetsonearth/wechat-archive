"""Turn one WeChat 4.x message row into a readable, searchable record.

Every kind gets a one-line `text` an agent can read without knowing WeChat's
XML. Links keep their URL, files keep name and size, forwarded chat records
are expanded, quotes keep what was quoted, media keep the hash that names the
local file.
"""
import html
import re
from xml.etree import ElementTree as ET

from . import proto
from .zstd import maybe_decompress

KIND = {1: 'text', 3: 'image', 34: 'voice', 42: 'card', 43: 'video', 47: 'sticker',
        48: 'location', 49: 'app', 50: 'call', 10000: 'system', 10002: 'system'}

APP_KIND = {3: 'music', 4: 'link', 5: 'link', 6: 'file', 8: 'sticker', 19: 'record', 24: 'note',
            33: 'miniprogram', 36: 'miniprogram', 40: 'record', 50: 'card', 51: 'channels',
            53: 'solitaire', 57: 'reply', 62: 'pat', 63: 'channels', 76: 'music', 87: 'announcement',
            2000: 'transfer', 2001: 'redpacket', 2003: 'redpacket'}

SENDER_PREFIX = re.compile(r'^([A-Za-z0-9_\-@.]{2,80}):\n')
HEX32 = re.compile(rb'[0-9a-f]{32}')


def _xml(body):
    start = body.find('<')
    if start < 0:
        return None
    s = body[start:]
    if '<!DOCTYPE' in s[:200].upper() or '<!ENTITY' in s.upper():
        return None
    try:
        return ET.fromstring(s)
    except ET.ParseError:
        # Some clients emit control characters; strip and retry once.
        try:
            return ET.fromstring(re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', s))
        except ET.ParseError:
            return None


def _t(el, path):
    if el is None:
        return ''
    v = el.findtext(path)
    return (v or '').strip()


def _seconds(ms):
    try:
        return round(int(ms) / 1000)
    except (TypeError, ValueError):
        return None


def _size(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ''
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('%d %s' % (n, unit)) if unit == 'B' else ('%.1f %s' % (n, unit))
        n /= 1024.0


def media_hash(packed):
    """The 32-hex name WeChat gives the local image/video file, from packed_info_data."""
    if not packed:
        return None
    m = HEX32.search(packed)
    return m.group().decode() if m else None


def _record_items(appmsg):
    """Expand a forwarded chat record (appmsg type 19/40) into lines."""
    raw = _t(appmsg, 'recorditem')
    if not raw:
        return []
    tree = _xml(raw) if raw.lstrip().startswith('<') else _xml(html.unescape(raw))
    items = []
    if tree is None:
        return items
    for item in tree.iter('dataitem'):
        who = _t(item, 'sourcename')
        when = _t(item, 'sourcetime')
        dtype = item.get('datatype', '')
        desc = _t(item, 'datadesc') or _t(item, 'datatitle')
        if dtype == '2':
            desc = '[图片]'
        elif dtype == '4':
            desc = '[视频]'
        elif dtype == '8':
            desc = '[文件] ' + _t(item, 'datatitle')
        elif dtype == '5':
            desc = '[链接] ' + (_t(item, 'datatitle') or desc) + ' ' + _t(item, 'link')
        elif dtype == '17':
            desc = '[聊天记录] ' + (_t(item, 'datatitle') or desc)
        items.append({'sender': who, 'time': when, 'text': desc.strip()})
    return items


def _sysmsg(tree, body):
    """Render <sysmsg> templates such as revokes, joins and pats into plain text."""
    if tree is None:
        return body.strip()
    if tree.tag == 'sysmsg':
        if tree.get('type') == 'revokemsg':
            return _t(tree, './/replacemsg') or '撤回了一条消息'
        template = _t(tree, './/template')
        if template:
            names = {}
            for link in tree.iter('link'):
                key = link.get('name')
                members = [_t(m, 'nickname') for m in link.iter('member')]
                plain = _t(link, 'plain')
                names[key] = '、'.join(x for x in members if x) or plain
            return re.sub(r'\$(\w+)\$', lambda m: names.get(m.group(1), ''), template).strip()
        return ' '.join(x.strip() for x in tree.itertext() if x.strip())[:500]
    return ' '.join(x.strip() for x in tree.itertext() if x.strip())[:500] or body.strip()


def decode(local_type, raw, packed=None, is_group=False):
    """Return dict(kind, text, sender_hint, title, url, source, quote_*, file_*, media, extra, status)."""
    kind_code, sub = local_type & 0xffffffff, local_type >> 32
    out = {'type': kind_code, 'subtype': sub, 'kind': KIND.get(kind_code, 'other'),
           'text': '', 'sender_hint': None, 'title': None, 'url': None, 'source': None,
           'quote_sender': None, 'quote_text': None, 'file_name': None, 'file_size': None,
           'media': None, 'extra': None, 'status': 'ok'}
    try:
        if isinstance(raw, str):
            raw = raw.encode('utf-8')
        body = maybe_decompress(raw or b'').decode('utf-8', 'replace')
    except Exception:
        out['status'] = 'undecodable'
        out['text'] = '[无法解码]'
        return out
    if is_group:
        m = SENDER_PREFIX.match(body)
        if m:
            out['sender_hint'] = m.group(1)
            body = body[m.end():]
    k = kind_code
    if k == 1:
        out['text'] = body
        return out
    tree = _xml(body)
    if k == 3:
        out['text'] = '[图片]'
        out['media'] = media_hash(packed)
    elif k == 34:
        sec = _seconds(tree.find('.//voicemsg').get('voicelength')) if tree is not None and tree.find('.//voicemsg') is not None else None
        out['text'] = '[语音 %s秒]' % sec if sec is not None else '[语音]'
        out['extra'] = {'seconds': sec}
    elif k == 43:
        v = tree.find('.//videomsg') if tree is not None else None
        sec = v.get('playlength') if v is not None else None
        out['text'] = '[视频 %s秒]' % sec if sec else '[视频]'
        out['media'] = media_hash(packed)
        out['extra'] = {'seconds': int(sec) if sec and sec.isdigit() else None}
    elif k == 47:
        out['text'] = '[表情]'
    elif k == 48:
        loc = tree.find('.//location') if tree is not None else None
        if loc is not None:
            place = ' '.join(x for x in (loc.get('poiname'), loc.get('label')) if x)
            out['text'] = '[位置] ' + place
            out['extra'] = {'x': loc.get('x'), 'y': loc.get('y')}
        else:
            out['text'] = '[位置]'
    elif k == 42:
        nick = tree.get('nickname') if tree is not None else ''
        out['text'] = '[名片] ' + (nick or '')
        out['extra'] = {'username': tree.get('username') if tree is not None else None}
    elif k == 50:
        out['kind'] = 'call'
        dur = _t(tree, './/duration') if tree is not None else ''
        out['text'] = '[通话%s]' % (' %s秒' % dur if dur and dur != '0' else '')
    elif k in (10000, 10002):
        out['text'] = _sysmsg(tree, body)
    elif k == 49:
        _app(tree, out)
    else:
        if tree is not None:
            out['text'] = '[%s]' % (tree.tag,)
        else:
            out['text'] = body.strip()[:200]
    if not out['text']:
        out['text'] = '[%s]' % out['kind']
    return out


def _app(tree, out):
    appmsg = None
    if tree is not None:
        appmsg = tree if tree.tag == 'appmsg' else tree.find('.//appmsg')
    if appmsg is None:
        out['status'] = 'unparsed'
        out['text'] = '[分享]'
        return
    atype = _t(appmsg, 'type')
    try:
        sub = int(atype) if atype else out['subtype']
    except ValueError:
        sub = out['subtype']
    out['subtype'] = sub
    kind = APP_KIND.get(sub, 'share')
    out['kind'] = kind
    title, des, url = _t(appmsg, 'title'), _t(appmsg, 'des'), _t(appmsg, 'url')
    source = _t(appmsg, 'sourcedisplayname') or _t(appmsg, 'appinfo/appname')
    out['title'], out['url'], out['source'] = title or None, url or None, source or None
    if kind == 'reply':
        out['kind'] = 'text'
        out['text'] = title
        ref = appmsg.find('refermsg')
        if ref is not None:
            out['quote_sender'] = _t(ref, 'displayname') or _t(ref, 'chatusr') or None
            rtype, content = _t(ref, 'type'), _t(ref, 'content')
            if rtype == '1':
                quoted = content
            elif rtype == '49':
                inner = _xml(content)
                quoted = ('[分享] ' + _t(inner, './/title')) if inner is not None else '[分享]'
            else:
                quoted = {'3': '[图片]', '34': '[语音]', '43': '[视频]', '47': '[表情]'}.get(rtype, '[消息]')
            out['quote_text'] = quoted[:1000]
        out['title'] = None
        return
    if kind == 'file':
        attach = appmsg.find('appattach')
        size = _t(attach, 'totallen') if attach is not None else ''
        out['file_name'] = title or None
        out['file_size'] = int(size) if size.isdigit() else None
        out['text'] = '[文件] %s%s' % (title, (' (%s)' % _size(size)) if size else '')
        return
    if kind == 'record':
        items = _record_items(appmsg)
        out['extra'] = {'items': items} if items else None
        lines = ['%s: %s' % (i['sender'], i['text']) for i in items[:200]]
        out['text'] = '[聊天记录] %s%s' % (title, ('\n' + '\n'.join(lines)) if lines else ('\n' + des if des else ''))
        return
    if kind == 'transfer':
        wc = appmsg.find('wcpayinfo')
        fee, memo = _t(wc, 'feedesc'), _t(wc, 'pay_memo')
        state = {'1': '发起', '3': '已收款', '4': '已退还', '8': '发起'}.get(_t(wc, 'paysubtype'), '')
        out['text'] = '[转账%s] %s%s' % (state, fee, (' ' + memo) if memo else '')
        return
    if kind == 'redpacket':
        wc = appmsg.find('wcpayinfo')
        out['text'] = '[红包] ' + (_t(wc, 'receivertitle') or _t(wc, 'sendertitle') or title)
        return
    if kind == 'pat':
        out['text'] = title or '[拍一拍]'
        out['url'] = None
        return
    if kind == 'announcement':
        out['text'] = '[群公告] ' + (_t(appmsg, 'textannouncement') or title)
        return
    if kind == 'solitaire':
        out['text'] = '[接龙] ' + title + ('\n' + des if des else '')
        return
    if kind == 'channels':
        feed = appmsg.find('.//finderFeed')
        nick = _t(feed, 'nickname') if feed is not None else ''
        desc = _t(feed, 'desc') if feed is not None else ''
        out['text'] = '[视频号] %s%s' % (nick, (': ' + desc) if desc else (' ' + title if title else ''))
        return
    label = {'link': '链接', 'miniprogram': '小程序', 'music': '音乐', 'note': '笔记',
             'card': '名片', 'sticker': '表情'}.get(kind, '分享')
    parts = [title]
    if des and des != title:
        parts.append(des)
    out['text'] = '[%s] %s' % (label, ' — '.join(p for p in parts if p))
    if source:
        out['text'] += ' (%s)' % source


def room_members(ext_buffer):
    """Group roster from chat_room.ext_buffer: [(username, group_display_name or None)]."""
    tree = proto.parse(ext_buffer or b'') or {}
    members = []
    for v in tree.get(1, []):
        if not isinstance(v, dict) or not v.get('msg'):
            continue
        m = v['msg']
        user = m.get(1, [{}])[0]
        name = m.get(2, [{}])[0]
        user = user.get('text') if isinstance(user, dict) else None
        name = name.get('text') if isinstance(name, dict) else None
        if user:
            members.append((user, name or None))
    return members
