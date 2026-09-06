"""Invoice processing: extraction (via a faked Claude response, exercising
the REAL parsing/validation code), field validation, duplicate detection,
amount-tolerance, date sanity, and reprocessing."""
from datetime import datetime, timedelta

import _companies_logic as companies_logic
import _extract_logic as extract_logic
import _files_logic as files_logic
import _invoices_logic as invoices_logic

GOOD_FIELDS = {
    'leverantor': 'Kontorsmaterial HB',
    'fakturanummer': 'F-2026-001',
    'ocr': '12345',
    'fakturadatum': '2026-08-01',
    'forfallodatum': '2026-08-31',
    'belopp_exkl_moms': 800.0,
    'moms': 200.0,
    'totalbelopp': 1000.0,
}


def _company(firm):
    _, payload = companies_logic.add_company(firm['token'], 'Klient AB')
    return payload['company']['id']


# ---------- Extraction (via faked Claude HTTP layer) ----------

def test_extract_returns_parsed_invoice_array(firm, sample_pdf_b64, fake_claude):
    fake_claude.set_response([GOOD_FIELDS])
    code, payload = extract_logic.extract_invoices(firm['token'], 'application/pdf', sample_pdf_b64, 'x.pdf')
    assert code == 200
    assert payload['invoices'] == [GOOD_FIELDS]


def test_extract_handles_multiple_invoices_in_one_file(firm, sample_pdf_b64, fake_claude):
    second = dict(GOOD_FIELDS, fakturanummer='F-2026-002')
    fake_claude.set_response([GOOD_FIELDS, second])
    code, payload = extract_logic.extract_invoices(firm['token'], 'application/pdf', sample_pdf_b64, 'x.pdf')
    assert code == 200
    assert len(payload['invoices']) == 2


def test_extract_finds_json_array_even_with_surrounding_prose(firm, sample_pdf_b64, fake_claude):
    """Claude sometimes wraps its JSON in a sentence despite the prompt
    asking for ONLY JSON - the regex-based extraction must still find and
    parse the array embedded in that prose."""
    fake_claude.set_raw_text('Here is the data you requested: [{"leverantor": "X"}] - let me know if you need anything else.')
    code, payload = extract_logic._extract_invoices_internal('application/pdf', sample_pdf_b64)
    assert code == 200
    assert payload['invoices'] == [{'leverantor': 'X'}]


def test_extract_returns_ai_no_json_for_bracket_less_response(firm, sample_pdf_b64, fake_claude):
    """Verified real behavior (not an assumption): the array-extraction
    regex requires literal square brackets, so a bare JSON OBJECT with no
    brackets anywhere (e.g. '{"leverantor": "X"}') does NOT get defensively
    wrapped into a list - it fails cleanly with ai_no_json. The
    `if not isinstance(items, list)` line further down in the real code is
    therefore unreachable in practice (a match on `\\[.*\\]` can only ever
    parse to a JSON array), which is a minor, harmless dead-code finding
    worth cleaning up later - not something this test should paper over by
    asserting behavior the code doesn't actually have."""
    fake_claude.set_raw_text('{"leverantor": "X"}')
    code, payload = extract_logic._extract_invoices_internal('application/pdf', sample_pdf_b64)
    assert code == 502
    assert payload['error'] == 'ai_no_json'


def test_extract_rejects_unsupported_media_type(firm, sample_pdf_b64, fake_claude):
    code, payload = extract_logic.extract_invoices(firm['token'], 'application/zip', sample_pdf_b64, 'x.zip')
    assert code == 400
    assert payload['error'] == 'unsupported_file_type'


def test_extract_handles_malformed_json_from_ai(firm, sample_pdf_b64, fake_claude):
    fake_claude.set_raw_text('this is not json at all, sorry')
    code, payload = extract_logic._extract_invoices_internal('application/pdf', sample_pdf_b64)
    assert code == 502
    assert payload['error'] == 'ai_no_json'


def test_extract_handles_invalid_json_inside_brackets(firm, sample_pdf_b64, fake_claude):
    fake_claude.set_raw_text('[{"leverantor": "broken", ]')
    code, payload = extract_logic._extract_invoices_internal('application/pdf', sample_pdf_b64)
    assert code == 502
    assert payload['error'] == 'ai_invalid_json'


def test_extract_handles_api_http_error(firm, sample_pdf_b64, fake_claude):
    fake_claude.set_http_error(code=529, body=b'{"error": "overloaded"}')
    code, payload = extract_logic._extract_invoices_internal('application/pdf', sample_pdf_b64)
    assert code == 502
    assert payload['error'] == 'ai_request_failed'


def test_extract_handles_network_timeout(firm, sample_pdf_b64, fake_claude):
    fake_claude.set_generic_error(TimeoutError('timed out'))
    code, payload = extract_logic._extract_invoices_internal('application/pdf', sample_pdf_b64)
    assert code == 502
    assert payload['error'] == 'ai_request_failed'


def test_extract_without_api_key_returns_clear_error(firm, sample_pdf_b64, monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    code, payload = extract_logic.extract_invoices(firm['token'], 'application/pdf', sample_pdf_b64, 'x.pdf')
    assert code == 503
    assert payload['error'] == 'ai_not_configured'


def test_extract_rejects_unauthenticated_request(sample_pdf_b64, fake_claude):
    fake_claude.set_response([GOOD_FIELDS])
    code, payload = extract_logic.extract_invoices('bad-key', 'application/pdf', sample_pdf_b64, 'x.pdf')
    assert code == 403


# ---------- Saving / validation ----------

def test_clean_invoice_saves_as_ok(firm):
    company_id = _company(firm)
    code, payload = invoices_logic.add_invoice(firm['token'], company_id, GOOD_FIELDS, None)
    assert code == 200
    assert payload['invoice']['status'] == 'ok'
    assert payload['invoice']['review_reasons'] == []


def test_invoice_requires_company_id(firm):
    code, payload = invoices_logic.add_invoice(firm['token'], None, GOOD_FIELDS, None)
    assert code == 400
    assert payload['error'] == 'company_required'


def test_missing_required_field_flags_needs_review(firm):
    company_id = _company(firm)
    fields = dict(GOOD_FIELDS, fakturanummer='')
    code, payload = invoices_logic.add_invoice(firm['token'], company_id, fields, None)
    assert code == 200
    assert payload['invoice']['status'] == 'needs_review'
    assert 'Ett eller flera fält saknas' in payload['invoice']['review_reasons']


def test_amount_mismatch_flags_needs_review(firm):
    company_id = _company(firm)
    fields = dict(GOOD_FIELDS, totalbelopp=9999.0)
    code, payload = invoices_logic.add_invoice(firm['token'], company_id, fields, None)
    assert payload['invoice']['status'] == 'needs_review'
    assert any('Summan går inte ihop' in r for r in payload['invoice']['review_reasons'])


def test_amount_within_relative_tolerance_is_not_flagged(firm):
    """Tolerance is max(0.50 kr, 1% of total). For a 1000 kr invoice, 1%%
    is 10 kr, so an 8 kr rounding gap must NOT be flagged."""
    company_id = _company(firm)
    fields = dict(GOOD_FIELDS, belopp_exkl_moms=792.0, moms=200.0, totalbelopp=1000.0)  # off by 8kr
    code, payload = invoices_logic.add_invoice(firm['token'], company_id, fields, None)
    assert payload['invoice']['status'] == 'ok'


def test_amount_beyond_relative_tolerance_is_flagged(firm):
    company_id = _company(firm)
    fields = dict(GOOD_FIELDS, belopp_exkl_moms=780.0, moms=200.0, totalbelopp=1000.0)  # off by 20kr > 1%
    code, payload = invoices_logic.add_invoice(firm['token'], company_id, fields, None)
    assert payload['invoice']['status'] == 'needs_review'


def test_tiny_invoice_uses_the_50_ore_floor_not_1_percent(firm):
    """For a 10 kr invoice, 1%% would be 0.10 kr, but the floor is 0.50 kr -
    so a 0.30 kr gap must NOT be flagged even though it's > 1%% of total."""
    company_id = _company(firm)
    fields = dict(GOOD_FIELDS, belopp_exkl_moms=8.0, moms=1.7, totalbelopp=10.0)  # off by 0.30kr
    code, payload = invoices_logic.add_invoice(firm['token'], company_id, fields, None)
    assert payload['invoice']['status'] == 'ok'


def test_unreasonable_invoice_date_flagged(firm):
    company_id = _company(firm)
    old_date = (datetime.now() - timedelta(days=1000)).strftime('%Y-%m-%d')
    fields = dict(GOOD_FIELDS, fakturadatum=old_date)
    code, payload = invoices_logic.add_invoice(firm['token'], company_id, fields, None)
    assert any('Orimligt fakturadatum' in r for r in payload['invoice']['review_reasons'])


def test_duplicate_invoice_is_flagged(firm):
    company_id = _company(firm)
    invoices_logic.add_invoice(firm['token'], company_id, GOOD_FIELDS, None)
    code, payload = invoices_logic.add_invoice(firm['token'], company_id, GOOD_FIELDS, None)
    assert payload['invoice']['status'] == 'needs_review'
    assert any('dubblett' in r.lower() for r in payload['invoice']['review_reasons'])


def test_same_invoice_number_different_company_is_not_a_duplicate(firm):
    """Duplicate detection is scoped per client company, not per firm -
    two different clients can legitimately share a supplier+invoice-number
    coincidence (e.g. the supplier's own numbering) without cross-flagging."""
    _, comp1 = companies_logic.add_company(firm['token'], 'Klient A')
    _, comp2 = companies_logic.add_company(firm['token'], 'Klient B')
    invoices_logic.add_invoice(firm['token'], comp1['company']['id'], GOOD_FIELDS, None)
    code, payload = invoices_logic.add_invoice(firm['token'], comp2['company']['id'], GOOD_FIELDS, None)
    assert payload['invoice']['status'] == 'ok'


def test_editing_a_flagged_invoice_to_fix_it_clears_the_flag(firm):
    company_id = _company(firm)
    fields = dict(GOOD_FIELDS, totalbelopp=9999.0)
    _, payload = invoices_logic.add_invoice(firm['token'], company_id, fields, None)
    invoice_id = payload['invoice']['id']
    assert payload['invoice']['status'] == 'needs_review'

    code, payload = invoices_logic.update_invoice(firm['token'], invoice_id, {'totalbelopp': 1000.0})
    assert code == 200
    assert payload['invoice']['status'] == 'ok'


def test_updating_an_invoice_does_not_flag_itself_as_a_duplicate(firm):
    """Re-saving the SAME invoice via update() must not trip the duplicate
    check against itself - _validate() scans all saved invoices including
    the one being edited."""
    company_id = _company(firm)
    _, payload = invoices_logic.add_invoice(firm['token'], company_id, GOOD_FIELDS, None)
    invoice_id = payload['invoice']['id']

    code, payload = invoices_logic.update_invoice(firm['token'], invoice_id, {'ocr': '99999'})
    assert code == 200
    assert payload['invoice']['status'] == 'ok'


def test_update_appends_to_history(firm):
    company_id = _company(firm)
    _, payload = invoices_logic.add_invoice(firm['token'], company_id, GOOD_FIELDS, None)
    invoice_id = payload['invoice']['id']
    assert len(payload['invoice']['history']) == 1
    assert payload['invoice']['history'][0]['action'] == 'created'

    _, payload = invoices_logic.update_invoice(firm['token'], invoice_id, {'ocr': '1'})
    assert len(payload['invoice']['history']) == 2
    assert payload['invoice']['history'][1]['action'] == 'updated'


def test_delete_invoice_removes_it(firm):
    company_id = _company(firm)
    _, payload = invoices_logic.add_invoice(firm['token'], company_id, GOOD_FIELDS, None)
    invoice_id = payload['invoice']['id']
    invoices_logic.delete_invoice(firm['token'], invoice_id)
    remaining = invoices_logic._list_invoices_for(firm['uid'])['invoices']
    assert remaining == []


# ---------- Line items / VAT breakdown / reprocess ----------

def test_line_items_and_vat_breakdown_are_stored(firm):
    company_id = _company(firm)
    fields = dict(GOOD_FIELDS,
                  rader=[{'beskrivning': 'Papper', 'antal': 2, 'apris': 400.0, 'moms_procent': 25, 'belopp': 800.0}],
                  moms_uppdelning=[{'sats': 25, 'belopp': 200.0}])
    code, payload = invoices_logic.add_invoice(firm['token'], company_id, fields, None)
    assert payload['invoice']['rader'][0]['beskrivning'] == 'Papper'
    assert payload['invoice']['moms_uppdelning'][0]['sats'] == 25


def test_malformed_rader_from_ai_does_not_crash_save(firm):
    """If the AI returns garbage instead of a proper line-item list, saving
    must degrade gracefully (empty list), never throw."""
    company_id = _company(firm)
    fields = dict(GOOD_FIELDS, rader='not-a-list', moms_uppdelning=[{'sats': 'not-a-number'}])
    code, payload = invoices_logic.add_invoice(firm['token'], company_id, fields, None)
    assert code == 200
    assert payload['invoice']['rader'] == []
    assert payload['invoice']['moms_uppdelning'][0]['sats'] == 0.0


def test_reprocess_requires_a_stored_original_file(firm):
    company_id = _company(firm)
    _, payload = invoices_logic.add_invoice(firm['token'], company_id, GOOD_FIELDS, None)  # no file_id
    invoice_id = payload['invoice']['id']
    code, payload = invoices_logic.reprocess_invoice(firm['token'], invoice_id)
    assert code == 400
    assert payload['error'] == 'no_original_file'


def test_reprocess_backfills_line_items_from_original_file(firm, sample_pdf_b64, fake_claude):
    company_id = _company(firm)
    _, file_payload = files_logic.save_file(firm['token'], 'application/pdf', sample_pdf_b64, 'x.pdf')
    file_id = file_payload['file_id']

    _, payload = invoices_logic.add_invoice(firm['token'], company_id, GOOD_FIELDS, file_id)
    invoice_id = payload['invoice']['id']
    assert payload['invoice']['rader'] == []

    enriched = dict(GOOD_FIELDS, rader=[{'beskrivning': 'Toner', 'antal': 1, 'apris': 800.0, 'moms_procent': 25, 'belopp': 800.0}])
    fake_claude.set_response([enriched])

    code, payload = invoices_logic.reprocess_invoice(firm['token'], invoice_id)
    assert code == 200
    assert payload['invoice']['rader'][0]['beskrivning'] == 'Toner'
    assert payload['invoice']['history'][-1]['action'] == 'reprocessed'


def test_reprocess_matches_correct_invoice_by_number_in_multi_invoice_file(firm, sample_pdf_b64, fake_claude):
    company_id = _company(firm)
    _, file_payload = files_logic.save_file(firm['token'], 'application/pdf', sample_pdf_b64, 'x.pdf')
    file_id = file_payload['file_id']
    _, payload = invoices_logic.add_invoice(firm['token'], company_id, GOOD_FIELDS, file_id)
    invoice_id = payload['invoice']['id']

    other = dict(GOOD_FIELDS, fakturanummer='SOME-OTHER-INVOICE', rader=[{'beskrivning': 'wrong one'}])
    mine = dict(GOOD_FIELDS, rader=[{'beskrivning': 'correct one', 'antal': 1, 'apris': 1.0, 'moms_procent': 25, 'belopp': 1.0}])
    fake_claude.set_response([other, mine])

    code, payload = invoices_logic.reprocess_invoice(firm['token'], invoice_id)
    assert code == 200
    assert payload['invoice']['rader'][0]['beskrivning'] == 'correct one'
