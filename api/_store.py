"""Store client for Upstash Redis via REST API - same proven pattern as the
Bahaa Sax / Invoicer / hallak-demo projects. Stdlib only, no pip deps.

Hashes (not lists) are used everywhere here because invoices, companies and
files all need O(1) lookup-by-id and in-place updates (editing a saved
invoice, for example) - the same reasoning as Invoicer's data model.

Commands are sent as a JSON array in the POST body (Upstash's "single
command" REST call), not as URL path segments - the path-segment style
(`GET /hset/key/field/value`) breaks once a value is an actual invoice
photo/PDF instead of a short text field: percent-encoding the base64 data
into the URL roughly triples its size, and a multi-megabyte URL gets
rejected outright, crashing the whole function instead of failing cleanly.
"""
import json
import os
import urllib.error
import urllib.request


class StoreNotConfigured(Exception):
    pass


class StoreRequestFailed(Exception):
    pass


def _base():
    url = os.environ.get('KV_REST_API_URL')
    token = os.environ.get('KV_REST_API_TOKEN')
    if not url or not token:
        raise StoreNotConfigured('KV_REST_API_URL / KV_REST_API_TOKEN are not set on this deployment')
    return url.rstrip('/'), token


def _call(*parts):
    url, token = _base()
    body = json.dumps(list(parts)).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise StoreRequestFailed(f'{e.code}: {e.read().decode()[:300]}') from e
    except Exception as e:
        raise StoreRequestFailed(str(e)) from e


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
