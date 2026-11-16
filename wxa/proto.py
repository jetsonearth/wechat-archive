"""Tiny schema-less protobuf reader for WeChat's ext_buffer / packed_info blobs."""


def varint(buf, i):
    shift = result = 0
    while True:
        if i >= len(buf):
            raise ValueError('truncated varint')
        b = buf[i]
        i += 1
        result |= (b & 0x7f) << shift
        if not b & 0x80:
            return result, i
        shift += 7
        if shift > 63:
            raise ValueError('varint too long')


def fields(buf):
    """Yield (field_number, wire_type, value) for one message level."""
    i = 0
    while i < len(buf):
        tag, i = varint(buf, i)
        num, wt = tag >> 3, tag & 7
        if wt == 0:
            v, i = varint(buf, i)
        elif wt == 1:
            v, i = buf[i:i + 8], i + 8
        elif wt == 2:
            n, i = varint(buf, i)
            v, i = buf[i:i + n], i + n
        elif wt == 5:
            v, i = buf[i:i + 4], i + 4
        else:
            raise ValueError('unsupported wire type %d' % wt)
        if i > len(buf):
            raise ValueError('truncated field')
        yield num, wt, v


def parse(buf, depth=3):
    """Best-effort tree: {field: [values]}; nested messages parsed when they decode cleanly."""
    out = {}
    try:
        items = list(fields(buf))
    except ValueError:
        return None
    for num, wt, v in items:
        if wt == 2 and depth > 0:
            sub = parse(v, depth - 1) if v else None
            text = None
            try:
                text = v.decode('utf-8')
            except UnicodeDecodeError:
                pass
            v = {'bytes': v, 'text': text, 'msg': sub}
        out.setdefault(num, []).append(v)
    return out


def strings(buf):
    """All UTF-8 strings found at any depth (for loose lookups)."""
    found = []

    def walk(b, depth):
        try:
            items = list(fields(b))
        except ValueError:
            return
        for _, wt, v in items:
            if wt == 2:
                try:
                    found.append(v.decode('utf-8'))
                except UnicodeDecodeError:
                    pass
                if depth < 6 and v:
                    walk(v, depth + 1)

    walk(buf, 0)
    return found
