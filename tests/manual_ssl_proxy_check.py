# -*- coding: utf-8 -*-
"""手动端到端验证「跳过 SSL 证书验证」：
本地起一个自签名 HTTPS 服务器，模拟代理/梯子 MITM 拦截（证书不被信任）的场景。
- verify_ssl=True  -> 应报 SSL 证书错误且附带跳过的指引
- verify_ssl=False -> 应能连接成功（勾选跳过 SSL 后可用）

用法: python tests/manual_ssl_proxy_check.py
"""
from __future__ import annotations

import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from pet.chat.models import ProviderConfig
from pet.chat.providers import test_connection


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        self.rfile.read(length)
        body = json.dumps({'choices': [{'message': {'content': 'pong'}}]}).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _make_self_signed_cert(tmp: str):
    import subprocess, sys
    cert, key = tmp + '.crt', tmp + '.key'
    subprocess.run(
        [sys.executable, '-c', (
            'import subprocess,sys\n'
            'subprocess.run([sys.executable,"-m","pip","install","--quiet","trustme"])\n'
            'import trustme\n'
            'ca=trustme.CA(); cert=ca.issue_cert("127.0.0.1","localhost")\n'
            'chain = cert.cert_chain_pems if hasattr(cert, "cert_chain_pems") else [cert.cert_chain_pem]\n'
            f'chain[0].write_to_path({cert!r})\n'
            f'cert.private_key_pem.write_to_path({key!r})\n'
        )],
        check=True,
    )
    return cert, key


def main():
    import tempfile, os
    tmpdir = tempfile.mkdtemp(prefix='pet-ssl-check-')
    cert_path = os.path.join(tmpdir, 'server.crt')
    key_path = os.path.join(tmpdir, 'server.key')
    _make_self_signed_cert(os.path.join(tmpdir, 'server'))

    server = HTTPServer(('127.0.0.1', 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    base = f'https://127.0.0.1:{port}/v1'
    print(f'自签名 HTTPS 服务器已启动: {base}')

    cfg_verify = ProviderConfig('t', base_url=base, api_key='x', verify_ssl=True)
    ok, msg = test_connection(cfg_verify)
    print(f'\n[不勾选跳过 SSL] verify_ssl=True  -> ok={ok}  msg={msg}')
    assert not ok, '自签名证书下 verify_ssl=True 不应通过'
    assert 'SSL' in msg or '证书' in msg, '错误信息应包含 SSL/证书字样'
    assert '跳过 SSL 证书验证' in msg, '错误信息应附上勾选跳过的指引'
    print('  ✓ 报证书错误且附指引')

    cfg_skip = ProviderConfig('t', base_url=base, api_key='x', verify_ssl=False)
    ok, msg = test_connection(cfg_skip)
    print(f'\n[勾选跳过 SSL]   verify_ssl=False -> ok={ok}  msg={msg}')
    assert ok, '跳过校验后应能连接成功'
    print('  ✓ 跳过校验后连接成功（即代理/梯子 MITM 场景勾选后可用）')

    print('\n全部通过 ✅')


if __name__ == '__main__':
    main()
