"""Checks a dedicated Gmail inbox for new emailed invoices, once a day (via
Vercel Cron - see vercel.json), and runs each PDF/image attachment through
the exact same extract -> validate -> save pipeline used by manual uploads.

Uses Python's stdlib imaplib with a Gmail "app password" - same low-tech
approach as the SMTP sending in the Bahaa Sax project, no OAuth needed.

Needs three environment variables:
  MAIL_ADDRESS       - the dedicated Gmail address invoices are forwarded to
  MAIL_APP_PASSWORD  - a Gmail "app password" for that account
  CLIENT_KEY         - reused from the rest of the app (this runs as the
                       same trusted server process, so it authenticates
                       itself with the app's own key)

An email whose subject doesn't contain any company's code lands in a
fallback "Okategoriserat" company instead of being silently dropped, so the
byrå can re-assign it by hand later.
"""
import email
import imaplib
import os
from email.header import decode_header

import _companies_logic as companies_logic
import _files_logic as files_logic
import _extract_logic as extract_logic
import _invoices_logic as invoices_logic

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


def _get_or_create_uncategorized(key):
    code, payload = companies_logic.list_companies(key)
    for c in payload.get('companies', []):
        if c.get('code') == 'OKATEGORISERAT':
            return c
    code, payload = companies_logic.add_company(key, 'Okategoriserat')
    return payload.get('company')


def check_inbox():
    mail_address = os.environ.get('MAIL_ADDRESS')
    mail_password = os.environ.get('MAIL_APP_PASSWORD')
    client_key = os.environ.get('CLIENT_KEY')
    if not mail_address or not mail_password or not client_key:
        return 503, {'error': 'mail_not_configured'}

    results = {'checked': 0, 'processed': 0, 'invoices_added': 0, 'errors': []}

    try:
        conn = imaplib.IMAP4_SSL('imap.gmail.com')
        conn.login(mail_address, mail_password)
        conn.select('INBOX')
        status, data = conn.search(None, 'UNSEEN')
        if status != 'OK':
            return 502, {'error': 'imap_search_failed'}
        msg_ids = data[0].split()
    except Exception as e:
        return 502, {'error': 'imap_connect_failed', 'detail': str(e)}

    uncategorized = None

    for msg_id in msg_ids:
        results['checked'] += 1
        try:
            status, msg_data = conn.fetch(msg_id, '(BODY.PEEK[])')
            if status != 'OK':
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            subject = _decode(msg.get('Subject', ''))

            company = companies_logic.match_company_by_text(subject)
            if not company:
                if uncategorized is None:
                    uncategorized = _get_or_create_uncategorized(client_key)
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

                import base64
                b64data = base64.b64encode(payload).decode()

                fcode, fpayload = files_logic.save_file(client_key, content_type, b64data)
                if fcode != 200:
                    results['errors'].append(f'{subject}: kunde inte spara fil')
                    continue
                file_id = fpayload['file_id']

                ecode, epayload = extract_logic.extract_invoices(client_key, content_type, b64data)
                if ecode != 200:
                    results['errors'].append(f'{subject}: AI-läsning misslyckades ({epayload.get("error")})')
                    continue

                for fields in epayload.get('invoices', []):
                    icode, ipayload = invoices_logic.add_invoice(client_key, company['id'], fields, file_id)
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

    return 200, results
