"""Calls the Claude API (vision) to pull structured data out of a photographed
or scanned supplier invoice - including documents that bundle MORE THAN ONE
invoice (e.g. a multi-page statement), which is why this always asks for and
parses a JSON ARRAY, even for the single-invoice case.

Stdlib only (urllib), no pip deps needed on Vercel.

Needs one environment variable:
  ANTHROPIC_API_KEY - from console.anthropic.com
"""
import json
import os
import re
import urllib.error
import urllib.request

from _auth import verify_session
from _media import resolve_media_type, SUPPORTED
import _events_logic as events_logic
import _users_logic as users_logic

MODEL = 'claude-sonnet-5'

PROMPT = (
    "Du tittar på ett dokument som innehåller EN ELLER FLERA leverantörsfakturor "
    "(t.ex. ett kontoutdrag kan innehålla flera fakturor efter varandra - läs av "
    "varje faktura du hittar separat).\n\n"
    "Svara ENDAST med en giltig JSON-array, inget annat, ingen markdown-formatering. "
    "Varje element i arrayen är en faktura med exakt dessa fält:\n\n"
    "{\n"
    '  "leverantor": "leverantörens namn",\n'
    '  "fakturanummer": "fakturanumret",\n'
    '  "ocr": "OCR/referensnummer för betalning, tom sträng om det saknas",\n'
    '  "fakturadatum": "YYYY-MM-DD",\n'
    '  "forfallodatum": "YYYY-MM-DD",\n'
    '  "belopp_exkl_moms": 0.0,\n'
    '  "moms": 0.0,\n'
    '  "totalbelopp": 0.0,\n'
    '  "rader": [\n'
    '    {"beskrivning": "vad raden gäller", "antal": 1.0, "apris": 0.0, "moms_procent": 25, "belopp": 0.0}\n'
    "  ],\n"
    '  "moms_uppdelning": [\n'
    '    {"sats": 25, "belopp": 0.0}\n'
    "  ]\n"
    "}\n\n"
    "Om ett fält inte går att läsa, sätt det till en tom sträng (eller 0 för "
    "belopp) - gissa aldrig ett värde du inte kan se. Om dokumentet bara "
    "innehåller EN faktura, svara ändå med en array som har ETT element.\n\n"
    '"rader" är varje enskild produkt-/tjänsterad på fakturan om de går att '
    "urskilja - lämna som en tom lista [] om fakturan bara visar en "
    "totalsumma utan uppdelade rader, gissa aldrig ihop rader som inte syns. "
    '"moms_uppdelning" är momsbeloppet uppdelat per momssats om fakturan har '
    "fler än en sats (t.ex. både 25% och 12%) - annars räcker en post med "
    "hela momsbeloppet på den enda satsen, eller en tom lista [] om momssatsen "
    "inte går att avgöra."
)


def extract_invoices(key, media_type, base64_data, filename=None):
    uid = verify_session(key)
    if not uid:
        return 403, {'error': 'forbidden'}
    code, payload = _extract_invoices_internal(media_type, base64_data, filename)
    if code != 200:
        firm_name = users_logic.get_firm_name(uid)
        detail = payload.get('detail') or payload.get('error') or ''
        events_logic.log_event('extract_error', uid, firm_name, detail=f"{payload.get('error')}: {detail}"[:200])
    return code, payload


def _extract_invoices_internal(media_type, base64_data, filename=None):
    """No auth check - for server-side callers (the mail cron) that already
    know the trusted firm they're processing and have no session token."""
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return 503, {'error': 'ai_not_configured'}

    media_type = resolve_media_type(media_type, filename)
    if media_type not in SUPPORTED:
        return 400, {'error': 'unsupported_file_type'}

    if media_type == 'application/pdf':
        content_block = {
            'type': 'document',
            'source': {'type': 'base64', 'media_type': media_type, 'data': base64_data},
        }
    else:
        content_block = {
            'type': 'image',
            'source': {'type': 'base64', 'media_type': media_type, 'data': base64_data},
        }

    body = json.dumps({
        'model': MODEL,
        'max_tokens': 4096,
        'messages': [
            {
                'role': 'user',
                'content': [content_block, {'type': 'text', 'text': PROMPT}],
            }
        ],
    }).encode()

    req = urllib.request.Request(
        'https://api.anthropic.com/v1/messages',
        data=body,
        headers={
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json',
        },
        method='POST',
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        return 502, {'error': 'ai_request_failed', 'detail': detail}
    except Exception as e:
        return 502, {'error': 'ai_request_failed', 'detail': str(e)}

    try:
        text = result['content'][0]['text']
    except (KeyError, IndexError):
        return 502, {'error': 'ai_bad_response'}

    match = re.search(r'\[.*\]', text, re.DOTALL)
    if not match:
        return 502, {'error': 'ai_no_json', 'raw': text[:300]}

    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError:
        return 502, {'error': 'ai_invalid_json', 'raw': text[:300]}

    if not isinstance(items, list):
        items = [items]

    return 200, {'ok': True, 'invoices': items}
