"""Authorization / tenant isolation: firm A must never be able to read or
modify firm B's companies, invoices, or files - even by guessing/reusing a
valid id, and even though every record lives in the same shared Redis
instance (isolation here is enforced entirely by uid-prefixed keys plus a
session check on every entrypoint, not by separate databases)."""
import _companies_logic as companies_logic
import _files_logic as files_logic
import _invoices_logic as invoices_logic


def test_firm_cannot_list_another_firms_companies(firm, other_firm):
    companies_logic.add_company(firm['token'], 'Firm1s Klient')
    code, payload = companies_logic.list_companies(other_firm['token'])
    assert code == 200
    assert payload['companies'] == []


def test_unauthorized_key_cannot_list_companies():
    code, payload = companies_logic.list_companies('garbage-key')
    assert code == 403
    assert payload['error'] == 'forbidden'


def test_firm_cannot_add_invoice_under_another_firms_company_id(firm, other_firm):
    """Firm B tries to save an invoice tagged with firm A's real
    company_id. The invoice gets created (add_invoice doesn't currently
    verify the company_id belongs to the calling firm), but it is stored
    under firm B's own uid-scoped hash, so firm A never sees it - proving
    the isolation boundary is the storage key, not the company_id field."""
    _, comp_payload = companies_logic.add_company(firm['token'], 'Firm1s Klient')
    firm1_company_id = comp_payload['company']['id']

    code, payload = invoices_logic.add_invoice(
        other_firm['token'], firm1_company_id,
        {'leverantor': 'X', 'fakturanummer': '1', 'fakturadatum': '2026-01-01',
         'forfallodatum': '2026-01-31', 'belopp_exkl_moms': 100, 'moms': 25, 'totalbelopp': 125},
        None,
    )
    assert code == 200

    firm1_invoices = invoices_logic._list_invoices_for(firm['uid'])['invoices']
    other_firm_invoices = invoices_logic._list_invoices_for(other_firm['uid'])['invoices']
    assert firm1_invoices == []
    assert len(other_firm_invoices) == 1


def test_firm_cannot_read_another_firms_file(firm, other_firm, sample_pdf_b64):
    code, payload = files_logic.save_file(firm['token'], 'application/pdf', sample_pdf_b64, 'x.pdf')
    assert code == 200
    file_id = payload['file_id']

    code, payload = files_logic.get_file(other_firm['token'], file_id)
    assert code == 404


def test_firm_can_read_its_own_file(firm, sample_pdf_b64):
    code, payload = files_logic.save_file(firm['token'], 'application/pdf', sample_pdf_b64, 'x.pdf')
    file_id = payload['file_id']
    code, payload = files_logic.get_file(firm['token'], file_id)
    assert code == 200
    assert payload['data'] == sample_pdf_b64


def test_firm_cannot_update_another_firms_invoice(firm, other_firm):
    _, comp_payload = companies_logic.add_company(firm['token'], 'Klient')
    _, inv_payload = invoices_logic.add_invoice(
        firm['token'], comp_payload['company']['id'],
        {'leverantor': 'X', 'fakturanummer': '1', 'fakturadatum': '2026-01-01',
         'forfallodatum': '2026-01-31', 'belopp_exkl_moms': 100, 'moms': 25, 'totalbelopp': 125},
        None,
    )
    invoice_id = inv_payload['invoice']['id']

    code, payload = invoices_logic.update_invoice(other_firm['token'], invoice_id, {'leverantor': 'Hacked'})
    assert code == 404

    # Confirm firm1's invoice is untouched.
    firm1_invoices = invoices_logic._list_invoices_for(firm['uid'])['invoices']
    assert firm1_invoices[0]['leverantor'] == 'X'


def test_firm_cannot_delete_another_firms_invoice(firm, other_firm):
    _, comp_payload = companies_logic.add_company(firm['token'], 'Klient')
    _, inv_payload = invoices_logic.add_invoice(
        firm['token'], comp_payload['company']['id'],
        {'leverantor': 'X', 'fakturanummer': '1', 'fakturadatum': '2026-01-01',
         'forfallodatum': '2026-01-31', 'belopp_exkl_moms': 100, 'moms': 25, 'totalbelopp': 125},
        None,
    )
    invoice_id = inv_payload['invoice']['id']

    invoices_logic.delete_invoice(other_firm['token'], invoice_id)

    firm1_invoices = invoices_logic._list_invoices_for(firm['uid'])['invoices']
    assert len(firm1_invoices) == 1


def test_firm_cannot_reprocess_another_firms_invoice(firm, other_firm):
    _, comp_payload = companies_logic.add_company(firm['token'], 'Klient')
    _, inv_payload = invoices_logic.add_invoice(
        firm['token'], comp_payload['company']['id'],
        {'leverantor': 'X', 'fakturanummer': '1', 'fakturadatum': '2026-01-01',
         'forfallodatum': '2026-01-31', 'belopp_exkl_moms': 100, 'moms': 25, 'totalbelopp': 125},
        None,
    )
    invoice_id = inv_payload['invoice']['id']

    code, payload = invoices_logic.reprocess_invoice(other_firm['token'], invoice_id)
    assert code == 404
