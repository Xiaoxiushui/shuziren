"""Use a local fake provider; never send credentials or person data externally."""
import contextlib
import importlib.util
import json
import os
import sys
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
spec = importlib.util.spec_from_file_location('model_test_server', ROOT / 'server/server.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


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


class ModelTests(unittest.TestCase):
    def exercise(self, responses):
        requests = []

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, dict(self.headers), payload))
                status, body = responses[min(len(requests) - 1, len(responses) - 1)]
                encoded = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        class QuietApp(app.Handler):
            def log_message(self, *args):
                pass

        with running(Provider) as provider, mock.patch.dict(os.environ, {
            'MODEL_API_BASE_URL': provider + '/v1', 'MODEL_API_KEY': 'local-test-only',
            'MODEL_API_NAME': 'fake-local-model',
        }), running(QuietApp) as base:
            history = [{'role': 'system', 'content': 'must not forward'}] + [
                {'role': 'user' if i % 2 == 0 else 'assistant', 'content': f'{i}:' + 'x' * 600}
                for i in range(10)
            ]
            request = urllib.request.Request(base + '/api/chat', data=json.dumps({
                'profileId': 'another-demo', 'message': '喜欢做什么？', 'history': history,
            }).encode(), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=5) as response:
                result = json.load(response)
        return result, requests

    def test_live_http_contract_and_grounding(self):
        result, requests = self.exercise([(200, {'choices': [{'message': {'content': ' 本地模拟回答 '}}]})])
        self.assertEqual(result, {'ok': True, 'reply': '本地模拟回答', 'mode': 'live', 'source': 'model', 'fallback': False})
        self.assertEqual(len(requests), 1)
        path, headers, payload = requests[0]
        self.assertEqual(path, '/v1/chat/completions')
        self.assertEqual(headers['Authorization'], 'Bearer local-test-only')
        self.assertEqual(payload['model'], 'fake-local-model')
        messages = payload['messages']
        self.assertEqual(len(messages), 10)  # system + eight history items + question
        self.assertEqual(sum(item['role'] == 'system' for item in messages), 1)
        self.assertIn('周姨', messages[0]['content'])
        self.assertIn('不得编造', messages[0]['content'])
        self.assertTrue(all(len(item['content']) <= 500 for item in messages[1:-1]))
        self.assertNotIn('local-test-only', json.dumps(result))

    def test_transient_provider_failure_retries_then_succeeds(self):
        result, requests = self.exercise([(503, {}), (200, {'choices': [{'message': {'content': '恢复成功'}}]})])
        self.assertEqual(len(requests), 2)
        self.assertEqual(result['mode'], 'live')
        self.assertFalse(result['fallback'])

    def test_provider_failure_returns_current_approved_answer(self):
        result, requests = self.exercise([(503, {'error': 'local-test-only'})])
        self.assertEqual(len(requests), 2)
        self.assertEqual(result['source'], 'approved_profile')
        self.assertTrue(result['fallback'])
        self.assertIn('茉莉', result['reply'])
        self.assertNotIn('local-test-only', json.dumps(result))

    def test_malformed_provider_payloads_do_not_break_chat(self):
        for body in (b'not json', [], {'choices': [None]}, {'choices': [{'message': {'content': ''}}]}):
            with self.subTest(body=body):
                result, _ = self.exercise([(200, body)])
                self.assertTrue(result['ok'])
                self.assertTrue(result['fallback'])
                self.assertEqual(result['mode'], 'demo')

    def test_timeout_has_finite_attempts(self):
        profile = app.requested_profile('another-demo')
        with mock.patch.dict(os.environ, {**{key: 'local-test-only' for key in app.MODEL_ENV_KEYS},
                                           'MODEL_API_BASE_URL': 'http://127.0.0.1:1/v1'}), mock.patch.object(
            app.urllib.request, 'urlopen', side_effect=TimeoutError
        ) as opener, mock.patch.object(app.time, 'sleep'):
            self.assertIsNone(app.call_openai_compatible(profile, '你好', []))
            self.assertEqual(opener.call_count, 2)
            self.assertTrue(all(call.kwargs['timeout'] == 6 for call in opener.call_args_list))

    def test_invalid_model_address_degrades_without_exception(self):
        profile = app.requested_profile('another-demo')
        with mock.patch.dict(os.environ, {key: 'invalid-local-config' for key in app.MODEL_ENV_KEYS}), mock.patch.object(app.time, 'sleep'):
            self.assertIsNone(app.call_openai_compatible(profile, '你好', []))

    def test_malformed_history_roles_are_ignored(self):
        self.assertEqual(app.clean_history([
            {'role': [], 'content': 'bad'}, {'role': {}, 'content': 'bad'},
            {'role': 'system', 'content': 'bad'}, {'role': 'user', 'content': ' 你好 '},
        ]), [{'role': 'user', 'content': '你好'}])


if __name__ == '__main__':
    unittest.main()
