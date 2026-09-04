"""Store client for Upstash Redis via REST API - same proven pattern as the
Bahaa Sax / Invoicer / hallak-demo projects. Stdlib only, no pip deps.
"""
import json
import os
import urllib.parse
import urllib.request


class StoreNotConfigured(Exception):
    pass


def _base():
    url = os.environ.get('KV_REST_API_URL')
    token = os.environ.get('KV_REST_API_TOKEN')
    if not url or not token:
        raise StoreNotConfigured('KV_REST_API_URL / KV_REST_API_TOKEN are not set on this deployment')
    return url.rstrip('/'), token


def _call(*parts):
    url, token = _base()
    path = '/'.join(urllib.parse.quote(str(p), safe='') for p in parts)
    req = urllib.request.Request(f'{url}/{path}', headers={'Authorization': f'Bearer {token}'})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode())


def rpush(key, value):
    return _call('rpush', key, value)


def lrange(key, start=0, stop=-1):
    result = _call('lrange', key, start, stop)
    return result.get('result') or []


def lrem_by_value(key, value):
    return _call('lrem', key, 0, value)
