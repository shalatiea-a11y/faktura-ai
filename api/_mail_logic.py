"""Two ways a firm's connected Gmail inbox gets checked for new invoice
attachments, both running the same extract -> validate -> save pipeline
used by manual uploads:

1. **Push (near-instant)**: Gmail calls `users.watch()` and publishes a
   Google Cloud Pub/Sub message the moment new mail arrives; Pub/Sub then
   POSTs that to our `/api/gmail/push` webhook (see index.py), which calls
   `handle_push_notification()` below. This is the primary path once a
   firm connects Gmail.
2. **Daily poll (safety net + watch renewal)**: Vercel Cron (free plan
   allows once/day) calls `check_inbox()`, which re-lists each connected
   firm's recent mail as a backstop for anything a push notification might
   have missed, and - just as importantly - renews every firm's Gmail
   watch subscription, since those expire after ~7 days.

No IMAP, no app password: everything here uses the Gmail API with a
read-only OAuth scope. Already-seen message ids are tracked (in a small
per-firm hash) so nothing is processed twice even though the daily poll's
search window overlaps across runs.

An email whose subject doesn't contain any client company's code lands in a
fallback "Okategoriserat" company instead of being silently dropped, so the
byrå can re-assign it by hand later.
"""
import base64
import json
import os
import urllib.error
from email.header import decode_header, make_header

import _companies_logic as companies_logic
import _files_logic as files_logic
import _extract_logic as extract_logic
import _invoices_logic as invoices_logic
import _users_logic as users_logic
import _events_logic as events_logic
import _google_oauth as google_oauth
from _store import hget, hset, StoreNotConfigured

ALLOWED_TYPES = ('application/pdf', 'image/jpeg', 'image/png', 'image/webp', 'image/heic')
SEARCH_QUERY = 'has:attachment newer_than:2d'


def _decode_subject(raw):
    if not raw:
        return ''
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def _get_or_create_uncategorized(uid):
    for c in companies_logic._list_companies_for(uid).get('companies', []):
        if c.get('code') == 'OKATEGORISERAT':
            return c
    _, payload = companies_logic._add_company_for(uid, 'Okategoriserat')
    return payload.get('company')


def _already_processed(uid, message_id):
    try:
        return bool(hget(f'processed_gmail:{uid}', message_id))
    except StoreNotConfigured:
        return False


def _mark_processed(uid, message_id):
    try:
        hset(f'processed_gmail:{uid}', message_id, '1')
    except StoreNotConfigured:
        pass


def _iter_attachments(part):
    """Recursively walks a Gmail message payload for attachment parts -
    a message can nest multipart/mixed -> multipart/alternative -> parts."""
    filename = part.get('filename') or ''
    mime_type = part.get('mimeType') or ''
    body = part.get('body') or {}
    if filename and mime_type in ALLOWED_TYPES:
        yield filename, mime_type, body
    for sub in part.get('parts') or []:
        yield from _iter_attachments(sub)


def _process_one_message(uid, access_token, message_id, uncategorized_cache):
    """Fetches one Gmail message, matches it to a client company by
    subject, and saves every invoice found in its attachments. Shared by
    both the daily poll and the push-notification path so they can never
    drift apart in behaviour."""
    msg = google_oauth.get_message(access_token, message_id)
    headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
    subject = _decode_subject(headers.get('Subject', ''))

    company = companies_logic._match_company_by_text_for(uid, subject)
    if not company:
        if uncategorized_cache.get('company') is None:
            uncategorized_cache['company'] = _get_or_create_uncategorized(uid)
        company = uncategorized_cache['company']

    attachment_found = False
    invoices_added = 0
    errors = []

    for filename, mime_type, body in _iter_attachments(msg.get('payload', {})):
        if 'attachmentId' in body:
            raw_bytes = google_oauth.get_attachment_bytes(access_token, message_id, body['attachmentId'])
        elif body.get('data'):
            raw_bytes = google_oauth.b64url_decode(body['data'])
        else:
            continue
        attachment_found = True
        b64data = base64.b64encode(raw_bytes).decode()

        fcode, fpayload = files_logic._save_file_for(uid, mime_type, b64data, filename)
        if fcode != 200:
            errors.append(f'{subject}: kunde inte spara fil')
            continue
        file_id = fpayload['file_id']

        ecode, epayload = extract_logic._extract_invoices_internal(mime_type, b64data, filename)
        if ecode != 200:
            errors.append(f'{subject}: AI-läsning misslyckades ({epayload.get("error")})')
            continue

        for fields in epayload.get('invoices', []):
            icode, ipayload = invoices_logic._add_invoice_for(uid, company['id'], fields, file_id)
            if icode == 200:
                invoices_added += 1

    return attachment_found, invoices_added, errors


def _check_one_inbox(uid, refresh_token):
    results = {'checked': 0, 'processed': 0, 'invoices_added': 0, 'errors': []}
    client_id = os.environ.get('GOOGLE_CLIENT_ID')
    client_secret = os.environ.get('GOOGLE_CLIENT_SECRET')
    if not client_id or not client_secret:
        results['errors'].append('google_oauth_not_configured')
        return results

    try:
        token_data = google_oauth.refresh_access_token(client_id, client_secret, refresh_token)
        access_token = token_data['access_token']
    except Exception as e:
        results['errors'].append(f'token_refresh_failed: {e}')
        return results

    try:
        messages = google_oauth.list_messages(access_token, SEARCH_QUERY)
    except Exception as e:
        results['errors'].append(f'gmail_list_failed: {e}')
        return results

    uncategorized_cache = {'company': None}

    for msg_ref in messages:
        message_id = msg_ref['id']
        if _already_processed(uid, message_id):
            continue
        results['checked'] += 1
        try:
            attachment_found, added, errs = _process_one_message(uid, access_token, message_id, uncategorized_cache)
            results['invoices_added'] += added
            results['errors'].extend(errs)
            if attachment_found:
                results['processed'] += 1
            _mark_processed(uid, message_id)
        except Exception as e:
            results['errors'].append(str(e))

    return results


def check_inbox():
    firms = users_logic.list_gmail_connected_users()
    if not firms:
        return 200, {'checked_firms': 0, 'note': 'no firm has connected Gmail yet'}

    client_id = os.environ.get('GOOGLE_CLIENT_ID')
    client_secret = os.environ.get('GOOGLE_CLIENT_SECRET')
    topic_name = os.environ.get('GOOGLE_PUBSUB_TOPIC')

    per_firm = {}
    for user in firms:
        result = _check_one_inbox(user['id'], user['gmail_refresh_token'])
        per_firm[user['email']] = result
        if result['errors']:
            summary = f"{len(result['errors'])} fel, t.ex. {result['errors'][0]}"[:200]
            events_logic.log_event('mail_error', user['id'], user.get('firm_name', ''), user['email'], detail=summary)

        # Renew the Gmail push subscription (expires after ~7 days) - doing
        # this on every daily run means it never lapses. Not fatal if it
        # fails: the daily poll above still catches everything, just not
        # instantly, until the next successful renewal.
        if topic_name and client_id and client_secret:
            try:
                token_data = google_oauth.refresh_access_token(client_id, client_secret, user['gmail_refresh_token'])
                watch_result = google_oauth.watch(token_data['access_token'], topic_name)
                users_logic.save_watch_state(user['id'], watch_result.get('historyId'), watch_result.get('expiration'))
            except Exception as e:
                events_logic.log_event(
                    'mail_error', user['id'], user.get('firm_name', ''), user['email'],
                    detail=f'watch renewal failed: {e}'[:200],
                )

    return 200, {'checked_firms': len(firms), 'results': per_firm}


def handle_push_notification(gmail_email, notified_history_id):
    """Called from the /api/gmail/push webhook the moment Gmail tells us a
    connected mailbox changed. Does an incremental sync from the last known
    history id instead of re-listing the whole inbox, so this stays fast."""
    uid = users_logic.find_uid_by_gmail_email(gmail_email)
    if not uid:
        return  # not one of our connected mailboxes (or already disconnected)

    try:
        raw = hget('users', uid)
    except StoreNotConfigured:
        return
    if not raw:
        return
    user = json.loads(raw)
    refresh_token = user.get('gmail_refresh_token')
    if not refresh_token:
        return

    client_id = os.environ.get('GOOGLE_CLIENT_ID')
    client_secret = os.environ.get('GOOGLE_CLIENT_SECRET')
    if not client_id or not client_secret:
        return

    firm_name = user.get('firm_name', '')

    try:
        token_data = google_oauth.refresh_access_token(client_id, client_secret, refresh_token)
        access_token = token_data['access_token']
    except Exception as e:
        events_logic.log_event('mail_error', uid, firm_name, gmail_email, detail=f'push token refresh failed: {e}'[:200])
        return

    start_history_id = user.get('gmail_history_id')
    if not start_history_id:
        # First notification ever for this mailbox with no baseline to diff
        # against yet - just record where we are now; the daily poll (or
        # the next notification) will pick up from here.
        users_logic.save_watch_state(uid, notified_history_id, user.get('gmail_watch_expiration'))
        return

    try:
        history_data = google_oauth.list_history(access_token, start_history_id)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Gmail only keeps history for about a week - if our stored
            # baseline fell outside that window, fall back to a normal poll
            # and reset the baseline from the current profile state.
            _check_one_inbox(uid, refresh_token)
            try:
                profile = google_oauth.get_profile(access_token)
                users_logic.save_watch_state(uid, profile.get('historyId'), user.get('gmail_watch_expiration'))
            except Exception:
                pass
        else:
            events_logic.log_event('mail_error', uid, firm_name, gmail_email, detail=f'history fetch failed: {e}'[:200])
        return
    except Exception as e:
        events_logic.log_event('mail_error', uid, firm_name, gmail_email, detail=f'history fetch failed: {e}'[:200])
        return

    message_ids = set()
    for record in history_data.get('history') or []:
        for added in record.get('messagesAdded') or []:
            message_ids.add(added['message']['id'])

    uncategorized_cache = {'company': None}
    for message_id in message_ids:
        if _already_processed(uid, message_id):
            continue
        try:
            _process_one_message(uid, access_token, message_id, uncategorized_cache)
            _mark_processed(uid, message_id)
        except Exception as e:
            events_logic.log_event('mail_error', uid, firm_name, gmail_email, detail=str(e)[:200])

    latest_history_id = history_data.get('historyId') or notified_history_id
    users_logic.save_watch_state(uid, latest_history_id, user.get('gmail_watch_expiration'))
