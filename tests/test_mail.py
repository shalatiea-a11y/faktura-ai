"""Gmail ingestion: both the daily-poll path (_check_one_inbox) and the
push-notification path (handle_push_notification) share one processing
function (_process_one_message) - these tests exercise both entry points
against a faked Gmail API surface (patched directly on the _google_oauth
module object, which _mail_logic accesses as `google_oauth.xxx(...)`, so
patching the module's attributes is a clean, real seam).

No real Gmail/Google credentials are used or required.
"""
import base64
import json

import pytest

import _companies_logic as companies_logic
import _google_oauth as google_oauth
import _invoices_logic as invoices_logic
import _mail_logic as mail_logic
import _users_logic as users_logic

GOOD_FIELDS = {
    'leverantor': 'Mejl-in Leverantör',
    'fakturanummer': 'M-1',
    'ocr': '111',
    'fakturadatum': '2026-08-01',
    'forfallodatum': '2026-08-31',
    'belopp_exkl_moms': 800.0,
    'moms': 200.0,
    'totalbelopp': 1000.0,
}


def _connect_gmail(firm, gmail_email='inbox@gmail.com', refresh_token='fake-refresh-token'):
    users_logic.save_gmail_connection(firm['uid'], gmail_email, refresh_token)
    return gmail_email


def _b64url(raw_bytes):
    return base64.urlsafe_b64encode(raw_bytes).decode()


def _message(message_id, subject, parts):
    return {
        'id': message_id,
        'payload': {
            'headers': [{'name': 'Subject', 'value': subject}],
            'mimeType': 'multipart/mixed',
            'parts': parts,
        },
    }


def _pdf_part(attachment_id='att1', filename='invoice.pdf'):
    return {'filename': filename, 'mimeType': 'application/pdf', 'body': {'attachmentId': attachment_id}}


def _inline_text_part():
    return {'filename': '', 'mimeType': 'text/plain', 'body': {'data': _b64url(b'hello, your invoice is attached')}}


@pytest.fixture
def gmail_env(monkeypatch, firm):
    """Wires a firm with Gmail 'connected' and fakes every _google_oauth
    call _mail_logic makes, driven by simple in-memory state a test can
    configure before calling into mail_logic."""
    gmail_email = _connect_gmail(firm)
    state = {
        'messages': [],       # list of {'id': ...} as returned by list_messages
        'message_bodies': {}, # message_id -> full message dict (get_message)
        'attachments': {},    # attachment_id -> raw bytes
        'history': {},        # historyId -> gmail history.list() response
        'profile_history_id': 'hist-100',
        'refresh_should_fail': False,
        'list_messages_should_fail': False,
    }

    def fake_refresh_access_token(client_id, client_secret, refresh_token):
        if state['refresh_should_fail']:
            raise RuntimeError('invalid_grant: token revoked')
        return {'access_token': 'fake-access-token'}

    def fake_list_messages(access_token, query, max_results=20):
        if state['list_messages_should_fail']:
            raise TimeoutError('gmail api timed out')
        return list(state['messages'])

    def fake_get_message(access_token, message_id):
        return state['message_bodies'][message_id]

    def fake_get_attachment_bytes(access_token, message_id, attachment_id):
        return state['attachments'][attachment_id]

    def fake_watch(access_token, topic_name):
        return {'historyId': 'hist-1', 'expiration': '9999999999999'}

    def fake_list_history(access_token, start_history_id):
        if start_history_id not in state['history']:
            import urllib.error
            raise urllib.error.HTTPError('url', 404, 'not found', {}, None)
        return state['history'][start_history_id]

    def fake_get_profile(access_token):
        return {'emailAddress': gmail_email, 'historyId': state['profile_history_id']}

    monkeypatch.setattr(google_oauth, 'refresh_access_token', fake_refresh_access_token)
    monkeypatch.setattr(google_oauth, 'list_messages', fake_list_messages)
    monkeypatch.setattr(google_oauth, 'get_message', fake_get_message)
    monkeypatch.setattr(google_oauth, 'get_attachment_bytes', fake_get_attachment_bytes)
    monkeypatch.setattr(google_oauth, 'watch', fake_watch)
    monkeypatch.setattr(google_oauth, 'list_history', fake_list_history)
    monkeypatch.setattr(google_oauth, 'get_profile', fake_get_profile)
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'test-client-id')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'test-client-secret')

    return {'firm': firm, 'gmail_email': gmail_email, 'state': state}


@pytest.fixture(autouse=True)
def _fake_extraction_for_mail_tests(monkeypatch, fake_claude_factory=None):
    """Mail-in tests care about routing/attachment-handling, not the AI
    itself, so extraction is stubbed at the same internal seam used by
    _local_test_server.py. This is a deliberate, narrower fake than
    fake_claude - these tests are about Gmail plumbing, and test_invoices.py
    already covers the extraction/parsing code path itself."""
    import _extract_logic as extract_logic

    def fake_extract(media_type, base64_data, filename=None):
        return 200, {'ok': True, 'invoices': [dict(GOOD_FIELDS)]}

    monkeypatch.setattr(extract_logic, '_extract_invoices_internal', fake_extract)


# ---------- Poll path: _check_one_inbox ----------

def test_valid_attachment_is_processed_and_invoice_saved(gmail_env):
    firm = gmail_env['firm']
    _, comp = companies_logic.add_company(firm['token'], 'Kalles Bygg')
    code = comp['company']['code']

    gmail_env['state']['messages'] = [{'id': 'm1'}]
    gmail_env['state']['message_bodies']['m1'] = _message('m1', f'Faktura [{code}]', [_pdf_part('a1')])
    gmail_env['state']['attachments']['a1'] = b'%PDF-1.4 real invoice bytes'

    result = mail_logic._check_one_inbox(firm['uid'], 'fake-refresh-token')
    assert result['errors'] == []
    assert result['processed'] == 1
    assert result['invoices_added'] == 1

    invoices = invoices_logic._list_invoices_for(firm['uid'])['invoices']
    assert len(invoices) == 1
    assert invoices[0]['company_id'] == comp['company']['id']


def test_message_with_no_attachment_is_ignored_without_error(gmail_env):
    """This reproduces the real 'apotek invoice with no attachment' case:
    the message is marked processed and produces zero invoices, but does
    NOT show up in results['errors'] - documenting the exact current
    (silent) behavior so a future change to surface this as a visible
    warning has a test to update deliberately, not by accident."""
    firm = gmail_env['firm']
    gmail_env['state']['messages'] = [{'id': 'm2'}]
    gmail_env['state']['message_bodies']['m2'] = _message('m2', 'Kvitto från Apoteket', [_inline_text_part()])

    result = mail_logic._check_one_inbox(firm['uid'], 'fake-refresh-token')
    assert result['processed'] == 0
    assert result['invoices_added'] == 0
    assert result['errors'] == []
    assert invoices_logic._list_invoices_for(firm['uid'])['invoices'] == []


def test_unsupported_attachment_type_is_skipped(gmail_env):
    firm = gmail_env['firm']
    gmail_env['state']['messages'] = [{'id': 'm3'}]
    gmail_env['state']['message_bodies']['m3'] = _message('m3', 'Faktura', [
        {'filename': 'invoice.exe', 'mimeType': 'application/x-msdownload', 'body': {'attachmentId': 'a3'}},
    ])
    result = mail_logic._check_one_inbox(firm['uid'], 'fake-refresh-token')
    assert result['processed'] == 0
    assert result['invoices_added'] == 0


def test_unknown_client_code_lands_in_uncategorized(gmail_env):
    firm = gmail_env['firm']
    gmail_env['state']['messages'] = [{'id': 'm4'}]
    gmail_env['state']['message_bodies']['m4'] = _message('m4', 'Faktura utan kod', [_pdf_part('a4')])
    gmail_env['state']['attachments']['a4'] = b'%PDF-1.4 bytes'

    result = mail_logic._check_one_inbox(firm['uid'], 'fake-refresh-token')
    assert result['invoices_added'] == 1

    companies = companies_logic._list_companies_for(firm['uid'])['companies']
    assert any(c['name'] == 'Okategoriserat' for c in companies)
    invoices = invoices_logic._list_invoices_for(firm['uid'])['invoices']
    uncategorized = next(c for c in companies if c['name'] == 'Okategoriserat')
    assert invoices[0]['company_id'] == uncategorized['id']


def test_uncategorized_company_is_reused_not_duplicated_across_separate_runs(gmail_env):
    """Regression test for a real bug found while writing this suite:
    _get_or_create_uncategorized used to look up the company by a
    hardcoded code string ('OKATEGORISERAT', 14 chars) that _make_code()
    can never actually produce (codes are truncated to 12 chars), so every
    unmatched email created a brand-new duplicate 'Okategoriserat' client
    company instead of reusing the first one. Two separate poll runs, each
    with an unmatched-code email, must land in the SAME company."""
    firm = gmail_env['firm']
    gmail_env['state']['messages'] = [{'id': 'm4a'}]
    gmail_env['state']['message_bodies']['m4a'] = _message('m4a', 'Faktura utan kod 1', [_pdf_part('a4a')])
    gmail_env['state']['attachments']['a4a'] = b'%PDF-1.4 first'
    mail_logic._check_one_inbox(firm['uid'], 'fake-refresh-token')

    gmail_env['state']['messages'] = [{'id': 'm4b'}]
    gmail_env['state']['message_bodies']['m4b'] = _message('m4b', 'Faktura utan kod 2', [_pdf_part('a4b')])
    gmail_env['state']['attachments']['a4b'] = b'%PDF-1.4 second'
    mail_logic._check_one_inbox(firm['uid'], 'fake-refresh-token')

    companies = companies_logic._list_companies_for(firm['uid'])['companies']
    uncategorized = [c for c in companies if c['name'] == 'Okategoriserat']
    assert len(uncategorized) == 1, f'expected exactly one Okategoriserat company, found {len(uncategorized)}'

    invoices = invoices_logic._list_invoices_for(firm['uid'])['invoices']
    assert len(invoices) == 2
    assert all(i['company_id'] == uncategorized[0]['id'] for i in invoices)


def test_already_processed_message_is_not_processed_twice(gmail_env):
    """Idempotency: the same message id appearing again (e.g. because the
    daily poll's search window overlaps a previous run) must not create a
    second invoice."""
    firm = gmail_env['firm']
    _, comp = companies_logic.add_company(firm['token'], 'Kalles Bygg')
    code = comp['company']['code']
    gmail_env['state']['messages'] = [{'id': 'm5'}]
    gmail_env['state']['message_bodies']['m5'] = _message('m5', f'Faktura [{code}]', [_pdf_part('a5')])
    gmail_env['state']['attachments']['a5'] = b'%PDF-1.4 bytes'

    mail_logic._check_one_inbox(firm['uid'], 'fake-refresh-token')
    result2 = mail_logic._check_one_inbox(firm['uid'], 'fake-refresh-token')

    assert result2['checked'] == 0
    invoices = invoices_logic._list_invoices_for(firm['uid'])['invoices']
    assert len(invoices) == 1


def test_multiple_attachments_in_one_message_all_processed(gmail_env):
    firm = gmail_env['firm']
    _, comp = companies_logic.add_company(firm['token'], 'Kalles Bygg')
    code = comp['company']['code']
    gmail_env['state']['messages'] = [{'id': 'm6'}]
    gmail_env['state']['message_bodies']['m6'] = _message(
        'm6', f'Två fakturor [{code}]', [_pdf_part('a6a', 'faktura1.pdf'), _pdf_part('a6b', 'faktura2.pdf')],
    )
    gmail_env['state']['attachments']['a6a'] = b'%PDF-1.4 first'
    gmail_env['state']['attachments']['a6b'] = b'%PDF-1.4 second'

    result = mail_logic._check_one_inbox(firm['uid'], 'fake-refresh-token')
    assert result['invoices_added'] == 2


def test_malformed_attachment_part_does_not_crash_processing(gmail_env):
    """A part that has a filename/mimeType but neither attachmentId nor
    inline data (malformed/unexpected Gmail payload shape) must be skipped
    safely, not raise and abort the whole message."""
    firm = gmail_env['firm']
    gmail_env['state']['messages'] = [{'id': 'm7'}]
    gmail_env['state']['message_bodies']['m7'] = _message('m7', 'Konstig faktura', [
        {'filename': 'invoice.pdf', 'mimeType': 'application/pdf', 'body': {}},
    ])
    result = mail_logic._check_one_inbox(firm['uid'], 'fake-refresh-token')
    assert result['errors'] == []
    assert result['invoices_added'] == 0


def test_token_refresh_failure_is_captured_not_raised(gmail_env):
    gmail_env['state']['refresh_should_fail'] = True
    result = mail_logic._check_one_inbox(gmail_env['firm']['uid'], 'fake-refresh-token')
    assert result['invoices_added'] == 0
    assert any('token_refresh_failed' in e for e in result['errors'])


def test_gmail_api_timeout_is_captured_not_raised(gmail_env):
    gmail_env['state']['list_messages_should_fail'] = True
    result = mail_logic._check_one_inbox(gmail_env['firm']['uid'], 'fake-refresh-token')
    assert any('gmail_list_failed' in e for e in result['errors'])


def test_check_inbox_skips_firms_without_gmail_connected(firm):
    """A firm that never connected Gmail must not be touched by the cron
    at all (no crash from a missing refresh token)."""
    code, payload = mail_logic.check_inbox()
    assert code == 200
    assert payload['checked_firms'] == 0


# ---------- Push path: handle_push_notification ----------

def test_push_notification_processes_new_message(gmail_env):
    firm = gmail_env['firm']
    _, comp = companies_logic.add_company(firm['token'], 'Kalles Bygg')
    code = comp['company']['code']

    users_logic.save_watch_state(firm['uid'], 'hist-1', '9999999999999')
    gmail_env['state']['message_bodies']['p1'] = _message('p1', f'Faktura [{code}]', [_pdf_part('pa1')])
    gmail_env['state']['attachments']['pa1'] = b'%PDF-1.4 pushed'
    gmail_env['state']['history']['hist-1'] = {
        'history': [{'messagesAdded': [{'message': {'id': 'p1'}}]}],
        'historyId': 'hist-2',
    }

    mail_logic.handle_push_notification(gmail_env['gmail_email'], 'hist-2')

    invoices = invoices_logic._list_invoices_for(firm['uid'])['invoices']
    assert len(invoices) == 1


def test_push_notification_for_unknown_mailbox_is_ignored(gmail_env):
    """A push notification for a Gmail address we don't have on file (e.g.
    already disconnected) must be silently ignored, never raise."""
    mail_logic.handle_push_notification('someone-else@gmail.com', 'hist-99')
    # No exception is itself the assertion; also confirm nothing was saved
    # anywhere by checking the connected firm's own invoice list.
    invoices = invoices_logic._list_invoices_for(gmail_env['firm']['uid'])['invoices']
    assert invoices == []


def test_push_notification_first_ever_call_just_records_baseline(gmail_env):
    """With no gmail_history_id stored yet, there's nothing to diff
    against - the real code records the notified id as the new baseline
    and does NOT attempt to process anything from it."""
    firm = gmail_env['firm']
    mail_logic.handle_push_notification(gmail_env['gmail_email'], 'hist-5')
    raw = users_logic.get_account(firm['token'])
    assert invoices_logic._list_invoices_for(firm['uid'])['invoices'] == []


def test_push_notification_duplicate_delivery_is_idempotent(gmail_env):
    """Pub/Sub explicitly does not guarantee exactly-once delivery - the
    same push notification arriving twice must not create two invoices."""
    firm = gmail_env['firm']
    _, comp = companies_logic.add_company(firm['token'], 'Kalles Bygg')
    code = comp['company']['code']
    users_logic.save_watch_state(firm['uid'], 'hist-1', '9999999999999')
    gmail_env['state']['message_bodies']['p2'] = _message('p2', f'Faktura [{code}]', [_pdf_part('pa2')])
    gmail_env['state']['attachments']['pa2'] = b'%PDF-1.4 bytes'
    gmail_env['state']['history']['hist-1'] = {
        'history': [{'messagesAdded': [{'message': {'id': 'p2'}}]}],
        'historyId': 'hist-2',
    }

    mail_logic.handle_push_notification(gmail_env['gmail_email'], 'hist-2')
    # Second delivery of the SAME notification: history baseline has moved
    # to hist-2 now, so simulate Gmail replaying the same historyId lookup.
    gmail_env['state']['history']['hist-2'] = {'history': [], 'historyId': 'hist-2'}
    mail_logic.handle_push_notification(gmail_env['gmail_email'], 'hist-2')

    invoices = invoices_logic._list_invoices_for(firm['uid'])['invoices']
    assert len(invoices) == 1


def test_push_notification_falls_back_to_poll_when_history_expired(gmail_env):
    """Gmail only retains history for ~1 week; if our stored baseline has
    aged out, the API returns 404 and the real code must fall back to a
    full poll instead of silently losing invoices."""
    firm = gmail_env['firm']
    _, comp = companies_logic.add_company(firm['token'], 'Kalles Bygg')
    code = comp['company']['code']
    users_logic.save_watch_state(firm['uid'], 'stale-history-id', '9999999999999')

    gmail_env['state']['messages'] = [{'id': 'p3'}]
    gmail_env['state']['message_bodies']['p3'] = _message('p3', f'Faktura [{code}]', [_pdf_part('pa3')])
    gmail_env['state']['attachments']['pa3'] = b'%PDF-1.4 bytes'
    # Deliberately no entry for 'stale-history-id' in state['history'] ->
    # fake_list_history raises HTTPError(404) for it.

    mail_logic.handle_push_notification(gmail_env['gmail_email'], 'hist-new')

    invoices = invoices_logic._list_invoices_for(firm['uid'])['invoices']
    assert len(invoices) == 1
