import hashlib
import unittest
from unittest.mock import patch

import httpx
import app


MAGNET = 'magnet:?xt=urn:btih:' + 'a' * 40 + '&dn=Example%20file&tr=https%3A%2F%2Ftracker.example%2Fa%3Fx%3D1'
RAW_INFO = b'd4:name4:test6:lengthi1ee'
TORRENT = b'd4:info' + RAW_INFO + b'8:announce14:https://t.teste'


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def proxy_request(self, handler, path='/download', **kwargs):
        client_type = httpx.AsyncClient
        seen = []

        def upstream(request):
            seen.append(request)
            self.assertIn(request.url.scheme, ('http', 'https'))
            return handler(request)

        def client_factory(**options):
            return client_type(transport=httpx.MockTransport(upstream), **options)

        async with client_type(transport=httpx.ASGITransport(app=app.app), base_url='http://proxy.test') as client:
            with patch.object(app.httpx, 'AsyncClient', side_effect=client_factory):
                response = await client.request(kwargs.pop('method', 'GET'), path, **kwargs)
        return response, seen

    async def test_direct_and_chained_magnet_redirects(self):
        for status in (301, 302, 303, 307, 308):
            for chained in (False, True):
                with self.subTest(status=status, chained=chained):
                    def upstream(request):
                        if chained and request.url.path == '/download':
                            return httpx.Response(302, headers={'location': '/final'})
                        return httpx.Response(status, headers={'location': MAGNET})
                    response, seen = await self.proxy_request(upstream)
                    self.assertEqual(response.status_code, 302 if status in (307, 308) else status)
                    self.assertEqual(response.headers['location'], MAGNET)
                    self.assertEqual(response.content, b'')
                    self.assertEqual(len(seen), 2 if chained else 1)

    async def test_http_redirect_repairs_torrent_without_changing_info(self):
        def upstream(request):
            if request.url.path == '/download':
                return httpx.Response(302, headers={'location': 'torrent'})
            return httpx.Response(200, content=TORRENT, headers={'content-type': 'application/x-bittorrent'})
        response, seen = await self.proxy_request(upstream)
        self.assertEqual([request.url.path for request in seen], ['/download', '/torrent'])
        self.assertEqual(response.content, b'd8:announce14:https://t.test4:info' + RAW_INFO + b'e')
        info_start = response.content.index(b'4:info') + len(b'4:info')
        info_end = app._skip_elem(response.content, info_start)
        self.assertEqual(hashlib.sha1(response.content[info_start:info_end]).digest(), hashlib.sha1(RAW_INFO).digest())

    async def test_httpx_preserves_method_body_and_strips_cross_origin_auth(self):
        def upstream(request):
            if request.url.path == '/download':
                return httpx.Response(307, headers={'location': 'https://other.example/final'})
            return httpx.Response(200, content=b'ok')
        response, seen = await self.proxy_request(upstream, method='POST', content=b'payload', headers={'authorization': 'Bearer synthetic'})
        self.assertEqual(response.content, b'ok')
        self.assertEqual([request.method for request in seen], ['POST', 'POST'])
        self.assertEqual(seen[1].content, b'payload')
        self.assertNotIn('authorization', seen[1].headers)

    async def test_redirect_loop_stays_bounded(self):
        with self.assertRaises(httpx.TooManyRedirects):
            await self.proxy_request(lambda request: httpx.Response(302, headers={'location': '/download'}))

    async def test_search_payload_and_non_redirect_location_are_unchanged(self):
        for payload in (b'{"url":"' + MAGNET.encode() + b'"}', b'<url>' + MAGNET.encode() + b'</url>'):
            response, seen = await self.proxy_request(lambda request: httpx.Response(200, content=payload, headers={'location': MAGNET}), path='/search')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, payload)
            self.assertEqual(len(seen), 1)


if __name__ == '__main__':
    unittest.main()
