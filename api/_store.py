"""Store client for Upstash Redis via REST API - same proven pattern as the
Bahaa Sax / Invoicer / hallak-demo projects. Stdlib only, no pip deps.

Hashes (not lists) are used everywhere here because invoices, companies and
files all need O(1) lookup-by-id and in-place updates (editing a saved
invoice, for example) - the same reasoning as Invoicer's data model.
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
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def hset(key, field, value):
    return _call('hset', key, field, value)


def hget(key, field):
    result = _call('hget', key, field)
    return result.get('result')


def hgetall(key):
    result = _call('hgetall', key)
    flat = result.get('result') or []
    return dict(zip(flat[0::2], flat[1::2]))


def hdel(key, field):
    return _call('hdel', key, field)
