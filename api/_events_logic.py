"""Admin event feed - a chronological log of what's happening across every
firm (signups, logins, failed logins, and errors), so the owner can see
who's using the product and who's running into trouble without needing a
live notification for every single thing (that was explicitly not wanted -
this is a pull, not a push).

Stored as a single Redis LIST (append-only, capped) rather than a hash,
since this is a feed to read in order, not a record to look up by id.
"""
import json
import time

from _store import rpush, lrange, ltrim, StoreNotConfigured, StoreRequestFailed

MAX_EVENTS = 1000


def log_event(event_type, uid=None, firm_name='', email='', detail=''):
    entry = {
        'type': event_type,
        'uid': uid or '',
        'firm_name': firm_name or '',
        'email': email or '',
        'detail': detail or '',
        'ts': int(time.time()),
    }
    try:
        rpush('events', json.dumps(entry))
        ltrim('events', -MAX_EVENTS, -1)
    except (StoreNotConfigured, StoreRequestFailed):
        pass  # best-effort logging - never let this break the actual request


def list_events(limit=100):
    try:
        raw = lrange('events', -limit, -1)
    except (StoreNotConfigured, StoreRequestFailed):
        return []
    events = []
    for item in raw:
        try:
            events.append(json.loads(item))
        except json.JSONDecodeError:
            continue
    events.reverse()  # newest first
    return events
