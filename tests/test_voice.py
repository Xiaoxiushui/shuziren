"""用本地假供应商验证语音链路，测试过程中不访问真实阿里云接口。"""
import contextlib
import importlib.util
import io
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
spec = importlib.util.spec_from_file_location('voice_test_server', ROOT / 'server/server.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

TOKEN = 'local-test-token-only'
APPKEY = 'local-test-appkey'
def build_wav(frames=1600, rate=16000):
    """生成一段真实可解析的 16k/16bit/单声道静音 WAV，供假供应商返回。"""
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b'\x00\x00' * frames)
    return buffer.getvalue()


WAV = build_wav()
BROWSER_FALLBACK = {'ok': True, 'mode': 'browser', 'source': 'browser_speech', 'fallback': True}


@contextlib.contextmanager
def running(handler):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def post(base, path, data, headers):
    request = urllib.request.Request(base + path, data=data, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            return reply.status, dict(reply.headers), reply.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


class VoiceTests(unittest.TestCase):
    @contextlib.contextmanager
    def stack(self, asr=(200, {}), tts=(200, WAV), configured=True, voice=''):
        requests = []

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                # urllib 会把头名改成 X-Nls-Token 这种形式，统一转小写再断言。
                requests.append((self.path, {name.lower(): value for name, value in self.headers.items()}, body))
                status, payload = asr if '/asr' in self.path else tts
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        class QuietApp(app.Handler):
            def log_message(self, *args):
                pass

        with running(Provider) as provider:
            override = {
                'NLS_APPKEY': APPKEY if configured else '',
                'NLS_TOKEN': TOKEN if configured else '',
                'NLS_TTS_VOICE': voice,
                'NLS_ASR_URL': provider + '/stream/v1/asr',
                'NLS_TTS_URL': provider + '/stream/v1/tts',
            }
            with mock.patch.dict(os.environ, override), running(QuietApp) as base:
                yield base, requests

    def asr_call(self, base, audio=WAV, content_type='audio/wav'):
        return post(base, '/api/asr', audio, {'Content-Type': content_type})

    def tts_call(self, base, text='回来啦。慢慢说，我在这儿听着。'):
        return post(base, '/api/tts', json.dumps({'profileId': 'another-demo', 'text': text}).encode(),
                    {'Content-Type': 'application/json'})

    def test_asr_success_returns_recognized_text(self):
        with self.stack(asr=(200, {'task_id': 'local', 'result': ' 北京的天气。 ', 'status': 20000000})) as (base, requests):
            status, _, body = self.asr_call(base)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'ok': True, 'text': '北京的天气。'})
        self.assertEqual(len(requests), 1)
        path, headers, payload = requests[0]
        self.assertIn('appkey=' + APPKEY, path)
        self.assertIn('format=wav', path)
        self.assertIn('sample_rate=16000', path)
        self.assertEqual(headers['x-nls-token'], TOKEN)
        self.assertEqual(payload, WAV)

    def test_asr_empty_result_is_ok_with_empty_text(self):
        with self.stack(asr=(200, {'result': '', 'status': 20000000})) as (base, _):
            status, _, body = self.asr_call(base)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'ok': True, 'text': ''})

    def test_asr_provider_failure_returns_503_without_leaking_token(self):
        for failure in ((500, b'provider down'), (200, b'not json'), (200, {'status': 40000001, 'message': 'bad'})):
            with self.subTest(failure=failure), self.stack(asr=failure) as (base, _):
                status, _, body = self.asr_call(base)
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(body), {'ok': False, 'error': 'asr_unavailable'})
            self.assertNotIn(TOKEN, body.decode())

    def test_asr_without_configuration_returns_503_and_stays_offline(self):
        with self.stack(configured=False) as (base, requests):
            status, _, body = self.asr_call(base)
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body), {'ok': False, 'error': 'asr_unavailable'})
        self.assertEqual(requests, [])

    def test_asr_rejects_invalid_audio_input(self):
        with self.stack() as (base, _):
            wrong_type = self.asr_call(base, content_type='application/json')
            empty = self.asr_call(base, audio=b'')
        self.assertEqual(wrong_type[0], 400)
        self.assertEqual(json.loads(wrong_type[2])['error'], 'unsupported_audio_type')
        self.assertEqual(empty[0], 400)
        self.assertEqual(json.loads(empty[2])['error'], 'empty_audio')

    def test_tts_splits_sentences_and_inserts_pause(self):
        # 两句文本应拆成两次合成请求，并按 NLS_TTS_PAUSE_MS 插入静音后拼接。
        with self.stack(tts=(200, WAV)) as (base, requests):
            status, headers, body = self.tts_call(base)
        self.assertEqual(status, 200)
        self.assertIn('audio/wav', headers['Content-Type'])
        self.assertEqual(headers['X-TTS-Truncated'], '0')
        self.assertEqual([json.loads(item[2].decode())['text'] for item in requests], ['回来啦。', '慢慢说，我在这儿听着。'])
        path, req_headers, payload = requests[0]
        form = json.loads(payload.decode())
        self.assertTrue(path.endswith('/stream/v1/tts'))
        self.assertIn('application/json', req_headers['content-type'])
        self.assertEqual(form['appkey'], APPKEY)
        self.assertEqual(form['token'], TOKEN)
        self.assertEqual(form['format'], 'wav')
        self.assertEqual(form['sample_rate'], 16000)
        self.assertEqual(form['voice'], 'xiaoyun')
        with wave.open(io.BytesIO(body)) as reader:
            total = reader.getnframes() / reader.getframerate()
        # 两段 0.1 秒 + 默认 260ms 停顿
        self.assertAlmostEqual(total, 0.2 + 0.26, places=2)
        self.assertNotEqual(body, WAV)

    def test_tts_voice_override_is_forwarded(self):
        with self.stack(tts=(200, WAV), voice='zhiyan') as (base, requests):
            self.tts_call(base)
        form = json.loads(requests[0][2].decode())
        self.assertEqual(form['voice'], 'zhiyan')

    def test_tts_long_text_is_truncated_to_300_chars(self):
        long_text = '回' * 400
        with self.stack(tts=(200, WAV)) as (base, requests):
            status, headers, body = self.tts_call(base, text=long_text)
        self.assertEqual(status, 200)
        self.assertEqual(headers['X-TTS-Truncated'], '1')
        self.assertEqual(body, WAV)
        form = json.loads(requests[0][2].decode())
        self.assertEqual(len(form['text']), 300)

    def test_tts_failure_falls_back_to_browser_speech(self):
        for failure in ((500, b''), (200, b'{"error":"bad"}'), (200, b'')):
            with self.subTest(failure=failure), self.stack(tts=failure) as (base, _):
                status, headers, body = self.tts_call(base)
            self.assertEqual(status, 200)
            self.assertIn('application/json', headers['Content-Type'])
            self.assertEqual(json.loads(body), BROWSER_FALLBACK)
            self.assertNotIn(TOKEN, body.decode())

    def test_tts_without_configuration_keeps_browser_fallback(self):
        with self.stack(configured=False) as (base, requests):
            status, _, body = self.tts_call(base)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), BROWSER_FALLBACK)
        self.assertEqual(requests, [])

    def test_tts_validation_errors_are_unchanged(self):
        with self.stack() as (base, requests):
            too_long = self.tts_call(base, text='回' * 501)
            empty = self.tts_call(base, text='   ')
        self.assertEqual(too_long[0], 400)
        self.assertEqual(json.loads(too_long[2])['error'], 'text_too_long')
        self.assertEqual(empty[0], 400)
        self.assertEqual(json.loads(empty[2])['error'], 'empty_text')
        self.assertEqual(requests, [])

    def test_timeouts_are_finite_and_do_not_raise(self):
        with mock.patch.dict(os.environ, {'NLS_APPKEY': APPKEY, 'NLS_TOKEN': TOKEN}), mock.patch.object(
            app.voice.urllib.request, 'urlopen', side_effect=TimeoutError
        ) as opener:
            self.assertIsNone(app.voice.recognize(WAV))
            self.assertEqual(opener.call_args.kwargs['timeout'], 8)
            self.assertEqual(app.voice.synthesize('你好'), (None, False))
            self.assertEqual(opener.call_args.kwargs['timeout'], 12)
            self.assertEqual(opener.call_count, 2)

    def test_unconfigured_helpers_do_not_call_network(self):
        with mock.patch.dict(os.environ, {'NLS_APPKEY': '', 'NLS_TOKEN': ''}), mock.patch.object(
            app.voice.urllib.request, 'urlopen'
        ) as opener:
            self.assertFalse(app.voice.nls_configured())
            self.assertIsNone(app.voice.recognize(WAV))
            self.assertEqual(app.voice.synthesize('你好'), (None, False))
            self.assertEqual(opener.call_count, 0)


if __name__ == '__main__':
    unittest.main()
