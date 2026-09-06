"""File handling: upload, retrieval, invalid media type, missing file, size
limits."""
import base64

import _files_logic as files_logic


def test_upload_and_retrieve_roundtrip(firm, sample_pdf_b64):
    code, payload = files_logic.save_file(firm['token'], 'application/pdf', sample_pdf_b64, 'invoice.pdf')
    assert code == 200
    file_id = payload['file_id']

    code, payload = files_logic.get_file(firm['token'], file_id)
    assert code == 200
    assert payload['data'] == sample_pdf_b64
    assert payload['media_type'] == 'application/pdf'


def test_upload_rejects_empty_data(firm):
    code, payload = files_logic.save_file(firm['token'], 'application/pdf', '', 'x.pdf')
    assert code == 400
    assert payload['error'] == 'missing_fields'


def test_upload_rejects_unauthenticated_request(sample_pdf_b64):
    code, payload = files_logic.save_file('bad-key', 'application/pdf', sample_pdf_b64, 'x.pdf')
    assert code == 403


def test_get_missing_file_returns_404(firm):
    code, payload = files_logic.get_file(firm['token'], 'does-not-exist')
    assert code == 404


def test_get_file_rejects_unauthenticated_request(firm, sample_pdf_b64):
    _, payload = files_logic.save_file(firm['token'], 'application/pdf', sample_pdf_b64, 'x.pdf')
    code, _ = files_logic.get_file('bad-key', payload['file_id'])
    assert code == 403


def test_ambiguous_browser_media_type_resolved_from_filename(firm, sample_pdf_b64):
    """Browsers sometimes report application/octet-stream for a PDF (seen
    from some Android share-sheet flows) - the real fix relies on the
    filename extension to recover the correct type."""
    code, payload = files_logic.save_file(firm['token'], 'application/octet-stream', sample_pdf_b64, 'invoice.pdf')
    assert code == 200
    _, get_payload = files_logic.get_file(firm['token'], payload['file_id'])
    assert get_payload['media_type'] == 'application/pdf'


def test_oversized_file_is_rejected(firm):
    # MAX_FILE_BYTES * 1.4 is the base64-inflated cutoff - build something
    # comfortably past it without actually allocating tens of megabytes.
    huge = base64.b64encode(b'0' * (files_logic.MAX_FILE_BYTES + 1)).decode()
    oversized_b64 = huge * 2  # guarantee it exceeds the *1.4 threshold too
    code, payload = files_logic.save_file(firm['token'], 'application/pdf', oversized_b64, 'huge.pdf')
    assert code == 413
    assert payload['error'] == 'file_too_large'


def test_file_content_is_isolated_by_firm(firm, other_firm, sample_pdf_b64):
    _, payload = files_logic.save_file(firm['token'], 'application/pdf', sample_pdf_b64, 'secret.pdf')
    file_id = payload['file_id']
    code, _ = files_logic.get_file(other_firm['token'], file_id)
    assert code == 404
