"""Checks each firm's connected Gmail inbox (via OAuth - see
_google_oauth.py and the "Anslut Gmail" button in Inställningar) once a day
for new invoice attachments, and runs each one through the exact same
extract -> validate -> save pipeline used by manual uploads.

No IMAP, no app password: this uses the Gmail API with a read-only OAuth
scope, refreshed per run from the stored refresh token. Already-seen
message ids are tracked (in a small per-firm hash) so nothing is processed
twice even though the search window overlaps across daily runs - this also
means we never need write access to the mailbox just to mark things read.

An email whose subject doesn't contain any client company's code lands in a
fallback "Okategoriserat" company instead of being silently dropped, so the
byrå can re-assign it by hand later.
"""
import base64
import os
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

    uncategorized = None

    for msg_ref in messages:
        message_id = msg_ref['id']
        if _already_processed(uid, message_id):
            continue
        results['checked'] += 1
        try:
            msg = google_oauth.get_message(access_token, message_id)
            headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
            subject = _decode_subject(headers.get('Subject', ''))

            company = companies_logic._match_company_by_text_for(uid, subject)
            if not company:
                if uncategorized is None:
                    uncategorized = _get_or_create_uncategorized(uid)
                company = uncategorized

            attachment_found = False
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
                    results['errors'].append(f'{subject}: kunde inte spara fil')
                    continue
                file_id = fpayload['file_id']

                ecode, epayload = extract_logic._extract_invoices_internal(mime_type, b64data, filename)
                if ecode != 200:
                    results['errors'].append(f'{subject}: AI-läsning misslyckades ({epayload.get("error")})')
                    continue

                for fields in epayload.get('invoices', []):
                    icode, ipayload = invoices_logic._add_invoice_for(uid, company['id'], fields, file_id)
                    if icode == 200:
                        results['invoices_added'] += 1

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

    per_firm = {}
    for user in firms:
        result = _check_one_inbox(user['id'], user['gmail_refresh_token'])
        per_firm[user['email']] = result
        if result['errors']:
            summary = f"{len(result['errors'])} fel, t.ex. {result['errors'][0]}"[:200]
            events_logic.log_event('mail_error', user['id'], user.get('firm_name', ''), user['email'], detail=summary)

    return 200, {'checked_firms': len(firms), 'results': per_firm}
