"""Decoding of each message kind. Uses hand-written payloads, no real data."""
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wxa import decode


def d(local_type, body, packed=None, is_group=False):
    raw = body.encode('utf-8') if isinstance(body, str) else body
    return decode.decode(local_type, raw, packed, is_group)


def test_plain_text():
    r = d(1, '晚上一起吃饭吗')
    assert r['kind'] == 'text' and r['text'] == '晚上一起吃饭吗'


def test_group_sender_prefix_stripped():
    r = d(1, 'wxid_abc:\n到了吗', is_group=True)
    assert r['text'] == '到了吗' and r['sender_hint'] == 'wxid_abc'


def test_group_prefix_not_stripped_in_private():
    r = d(1, 'wxid_abc:\n到了吗', is_group=False)
    assert r['text'] == 'wxid_abc:\n到了吗'


def test_image_uses_packed_hash():
    packed = b'\x00\x01' + b'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6' + b'\x00'
    r = d(3, '<msg><img md5="x"/></msg>', packed)
    assert r['kind'] == 'image' and r['media'] == 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6'


def test_voice_seconds():
    r = d(34, '<msg><voicemsg voicelength="4200" /></msg>')
    assert r['kind'] == 'voice' and '4' in r['text'] and r['extra']['seconds'] == 4


def test_video_play_length():
    r = d(43, '<msg><videomsg playlength="12" /></msg>')
    assert r['kind'] == 'video' and r['extra']['seconds'] == 12


def test_location():
    r = d(48, '<msg><location x="22.5" y="113.9" label="深圳" poiname="南山科技园" /></msg>')
    assert r['kind'] == 'location' and '南山科技园' in r['text'] and r['extra']['x'] == '22.5'


def test_link_keeps_url_and_source():
    xml = ('<msg><appmsg><type>5</type><title>学术圈要完蛋了</title>'
           '<des>一篇小红书笔记</des><url>https://xhslink.com/abc</url>'
           '<sourcedisplayname>捉鱼不摸鱼</sourcedisplayname></appmsg></msg>')
    r = d(49, xml)
    assert r['kind'] == 'link'
    assert r['url'] == 'https://xhslink.com/abc'
    assert '学术圈要完蛋了' in r['text'] and '捉鱼不摸鱼' in r['text']


def test_file_name_and_size():
    xml = ('<msg><appmsg><type>6</type><title>合同.pdf</title>'
           '<appattach><totallen>2097152</totallen></appattach></appmsg></msg>')
    r = d(49, xml)
    assert r['kind'] == 'file' and r['file_name'] == '合同.pdf'
    assert r['file_size'] == 2097152 and 'MB' in r['text']


def test_quote_reply():
    xml = ('<msg><appmsg><type>57</type><title>对，就这样</title>'
           '<refermsg><type>1</type><displayname>张伟</displayname>'
           '<content>方案我看了</content></refermsg></appmsg></msg>')
    r = d(49, xml)
    assert r['kind'] == 'text' and r['text'] == '对，就这样'
    assert r['quote_sender'] == '张伟' and r['quote_text'] == '方案我看了'


def test_transfer():
    xml = ('<msg><appmsg><type>2000</type><wcpayinfo><paysubtype>1</paysubtype>'
           '<feedesc>¥200.00</feedesc><pay_memo>午饭</pay_memo></wcpayinfo></appmsg></msg>')
    r = d(49, xml)
    assert r['kind'] == 'transfer' and '¥200.00' in r['text'] and '午饭' in r['text']


def test_forwarded_record_expands():
    xml = ('<msg><appmsg><type>19</type><title>群聊的聊天记录</title><recorditem>'
           '&lt;recordinfo&gt;&lt;datalist&gt;'
           '&lt;dataitem datatype="1"&gt;&lt;sourcename&gt;Sam&lt;/sourcename&gt;'
           '&lt;datadesc&gt;账号 743281434988&lt;/datadesc&gt;&lt;/dataitem&gt;'
           '&lt;/datalist&gt;&lt;/recordinfo&gt;'
           '</recorditem></appmsg></msg>')
    r = d(49, xml)
    assert r['kind'] == 'record'
    assert '743281434988' in r['text']
    assert r['extra']['items'][0]['sender'] == 'Sam'


def test_system_revoke():
    r = d(10000, '<sysmsg type="revokemsg"><revokemsg><replacemsg>你撤回了一条消息</replacemsg></revokemsg></sysmsg>')
    assert r['kind'] == 'system' and '撤回' in r['text']


def test_zstd_body():
    payload = '这是一条被压缩的消息'.encode('utf-8')
    # fabricate a zstd frame via the library used in production
    from wxa import zstd as zmod
    import ctypes as C
    L = zmod._load()
    L.ZSTD_compressBound.argtypes = [C.c_size_t]
    L.ZSTD_compressBound.restype = C.c_size_t
    L.ZSTD_compress.argtypes = [C.c_void_p, C.c_size_t, C.c_void_p, C.c_size_t, C.c_int]
    L.ZSTD_compress.restype = C.c_size_t
    cap = L.ZSTD_compressBound(len(payload))
    buf = C.create_string_buffer(cap)
    n = L.ZSTD_compress(buf, cap, payload, len(payload), 3)
    frame = buf.raw[:n]
    r = d(1, frame)
    assert r['text'] == '这是一条被压缩的消息'


def test_undecodable_is_marked():
    r = d(1, b'\x28\xb5\x2f\xfd\xff\xff\xff\xff\xff')  # zstd magic, garbage body
    assert r['status'] == 'undecodable'


if __name__ == '__main__':
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    passed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except Exception:
            print('FAIL', fn.__name__)
            traceback.print_exc()
    print('%d/%d decode tests passed' % (passed, len(fns)))
    sys.exit(0 if passed == len(fns) else 1)
