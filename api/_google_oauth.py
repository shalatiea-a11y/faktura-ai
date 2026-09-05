"""Google OAuth (Gmail read-only) - lets a firm connect their Gmail inbox
with one click ("Anslut Gmail") instead of generating and pasting a Gmail
app password. Stdlib only (urllib), no google-api-python-client dependency,
same rule as the rest of this app.

Needs two environment variables from a Google Cloud OAuth client (a
one-time setup the owner does in Google Cloud Console - see README):
  GOOGLE_CLIENT_ID
  GOOGLE_CLIENT_SECRET

Scope requested is gmail.readonly only - never write/send/delete access -
so this can only ever list and read messages in a connected mailbox, never
change anything in it.
"""
import base64
import json
import urllib.error
import urllib.parse
import urllib.request

SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
GMAIL_API = 'https://gmail.googleapis.com/gmail/v1/users/me'


def build_auth_url(client_id, redirect_uri, state):
    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': SCOPE,
        'access_type': 'offline',
        'prompt': 'consent',  # forces a refresh_token every time, not just on first-ever consent
        'state': state,
    }
    return f'{AUTH_URL}?{urllib.parse.urlencode(params)}'


def _post_form(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body, headers={'Content-Type': 'application/x-www-form-urlencoded'}, method='POST',
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def exchange_code(client_id, client_secret, redirect_uri, code):
    return _post_form(TOKEN_URL, {
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri,
        'code': code,
        'grant_type': 'authorization_code',
    })


def refresh_access_token(client_id, client_secret, refresh_token):
    return _post_form(TOKEN_URL, {
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token',
    })


def _get(url, access_token):
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {access_token}'})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def _post_json(url, access_token, data):
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def watch(access_token, topic_name):
    """Registers (or renews - it's idempotent) a Gmail push subscription:
    Google will publish a Pub/Sub message to `topic_name` whenever this
    mailbox's INBOX changes. Expires after ~7 days, so this needs calling
    again before then (the daily cron does this automatically)."""
    return _post_json(f'{GMAIL_API}/watch', access_token, {'topicName': topic_name, 'labelIds': ['INBOX']})


def list_history(access_token, start_history_id):
    """Incremental sync since `start_history_id` - used when a push
    notification arrives, so we only fetch what's actually new instead of
    re-listing the whole inbox."""
    params = urllib.parse.urlencode({'startHistoryId': start_history_id, 'historyTypes': 'messageAdded'})
    return _get(f'{GMAIL_API}/history?{params}', access_token)


def get_profile(access_token):
    return _get(f'{GMAIL_API}/profile', access_token)


def list_messages(access_token, query, max_results=20):
    params = urllib.parse.urlencode({'q': query, 'maxResults': max_results})
    data = _get(f'{GMAIL_API}/messages?{params}', access_token)
    return data.get('messages') or []


def get_message(access_token, message_id):
    return _get(f'{GMAIL_API}/messages/{message_id}?format=full', access_token)


def get_attachment_bytes(access_token, message_id, attachment_id):
    data = _get(f'{GMAIL_API}/messages/{message_id}/attachments/{attachment_id}', access_token)
    return b64url_decode(data.get('data', ''))


def b64url_decode(s):
    padding = '=' * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)
