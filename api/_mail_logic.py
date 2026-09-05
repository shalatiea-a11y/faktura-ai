"""Checks each firm's dedicated Gmail inbox for new emailed invoices, once a
day (via Vercel Cron - see vercel.json), and runs each PDF/image attachment
through the exact same extract -> validate -> save pipeline used by manual
uploads.

Uses Python's stdlib imaplib with a Gmail "app password" - same low-tech
approach as the SMTP sending in the Bahaa Sax project, no OAuth needed.

Since accounts moved from one shared CLIENT_KEY to one account per firm,
mail credentials moved with them: each firm plugs in its own inbox from the
Inställningar page (_users_logic.py) instead of a single global env var
pair, and this cron loops over every firm that has done so.

An email whose subject doesn't contain any client company's code lands in a
fallback "Okategoriserat" company instead of being silently dropped, so the
byrå can re-assign it by hand later.
"""
import base64
import email
import imaplib
from email.header import decode_header

import _companies_logic as companies_logic
import _files_logic as files_logic
import _extract_logic as extract_logic
import _invoices_logic as invoices_logic
import _users_logic as users_logic
import _events_logic as events_logic

ALLOWED_TYPES = ('application/pdf', 'image/jpeg', 'image/png', 'image/webp', 'image/heic')


def _decode(value):
    if not value:
        return ''
    parts = decode_header(value)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or 'utf-8', errors='ignore'))
        else:
            out.append(text)
    return ''.join(out)


def _get_or_create_uncategorized(uid):
    for c in companies_logic._list_companies_for(uid).get('companies', []):
        if c.get('code') == 'OKATEGORISERAT':
            return c
    _, payload = companies_logic._add_company_for(uid, 'Okategoriserat')
    return payload.get('company')


def _check_one_inbox(uid, mail_address, mail_password):
    results = {'checked': 0, 'processed': 0, 'invoices_added': 0, 'errors': []}

    try:
        conn = imaplib.IMAP4_SSL('imap.gmail.com')
        conn.login(mail_address, mail_password)
        conn.select('INBOX')
        status, data = conn.search(None, 'UNSEEN')
        if status != 'OK':
            results['errors'].append('imap_search_failed')
            return results
        msg_ids = data[0].split()
    except Exception as e:
        results['errors'].append(f'imap_connect_failed: {e}')
        return results

    uncategorized = None

    for msg_id in msg_ids:
        results['checked'] += 1
        try:
            status, msg_data = conn.fetch(msg_id, '(BODY.PEEK[])')
            if status != 'OK':
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            subject = _decode(msg.get('Subject', ''))

            company = companies_logic._match_company_by_text_for(uid, subject)
            if not company:
                if uncategorized is None:
                    uncategorized = _get_or_create_uncategorized(uid)
                company = uncategorized

            attachment_found = False
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type not in ALLOWED_TYPES:
                    continue
                filename = part.get_filename()
                if not filename:
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                attachment_found = True

                b64data = base64.b64encode(payload).decode()

                fcode, fpayload = files_logic._save_file_for(uid, content_type, b64data, filename)
                if fcode != 200:
                    results['errors'].append(f'{subject}: kunde inte spara fil')
                    continue
                file_id = fpayload['file_id']

                ecode, epayload = extract_logic._extract_invoices_internal(content_type, b64data, filename)
                if ecode != 200:
                    results['errors'].append(f'{subject}: AI-läsning misslyckades ({epayload.get("error")})')
                    continue

                for fields in epayload.get('invoices', []):
                    icode, ipayload = invoices_logic._add_invoice_for(uid, company['id'], fields, file_id)
                    if icode == 200:
                        results['invoices_added'] += 1

            if attachment_found:
                results['processed'] += 1
            conn.store(msg_id, '+FLAGS', '\\Seen')
        except Exception as e:
            results['errors'].append(str(e))

    try:
        conn.logout()
    except Exception:
        pass

    return results


def check_inbox():
    firms = users_logic.list_mail_enabled_users()
    if not firms:
        return 200, {'checked_firms': 0, 'note': 'no firm has configured a mail-in inbox yet'}

    per_firm = {}
    for user in firms:
        result = _check_one_inbox(user['id'], user['mail_address'], user['mail_app_password'])
        per_firm[user['email']] = result
        if result['errors']:
            summary = f"{len(result['errors'])} fel, t.ex. {result['errors'][0]}"[:200]
            events_logic.log_event('mail_error', user['id'], user.get('firm_name', ''), user['email'], detail=summary)

    return 200, {'checked_firms': len(firms), 'results': per_firm}
