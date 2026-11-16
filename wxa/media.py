"""Locate the on-disk file for an image/video/file message. Read-only."""
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))


def locate(account_base, chat_id, media_hash, ts, kind, file_name=None):
    """Best-effort path to a media file; returns an existing path or None.

    Images live under msg/attach/<md5(chat)>/<YYYY-MM>/Img/<hash>.dat (plus _t.dat
    thumbnail). Videos live under msg/video/<YYYY-MM>/<hash>.mp4. Files keep their
    original name under msg/file/<YYYY-MM>/.
    """
    from .source import md5
    month = datetime.fromtimestamp(ts, TZ).strftime('%Y-%m') if ts else None
    months = [month] if month else []
    # neighbours, since a message near a month boundary may sit in either folder
    if ts:
        for delta in (-1, 1):
            d = datetime.fromtimestamp(ts, TZ) + timedelta(days=5 * delta)
            months.append(d.strftime('%Y-%m'))
    base = account_base / 'msg'
    for mon in dict.fromkeys(months):
        if kind == 'image' and media_hash:
            d = base / 'attach' / md5(chat_id) / mon / 'Img'
            for name in (media_hash + '.dat', media_hash + '_t.dat'):
                p = d / name
                if p.exists():
                    return p
        elif kind == 'video' and media_hash:
            for name in (media_hash + '.mp4', media_hash + '_thumb.jpg'):
                p = base / 'video' / mon / name
                if p.exists():
                    return p
        elif kind in ('file', 'app') and file_name:
            p = base / 'file' / mon / file_name
            if p.exists():
                return p
    return None
