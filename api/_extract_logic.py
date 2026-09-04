"""Calls the Claude API (vision) to pull structured data out of a photographed
or scanned supplier invoice. Stdlib only (urllib), no pip deps needed on
Vercel - same approach as the rest of these projects.

Needs one environment variable:
  ANTHROPIC_API_KEY - from console.anthropic.com
"""
import json
import os
import re
import urllib.request

from _auth import check_key

MODEL = 'claude-sonnet-5'

PROMPT = (
    "Du tittar på en leverantörsfaktura. Läs ut följande fält och svara ENDAST "
    "med ett giltigt JSON-objekt, inget annat, ingen markdown-formatering:\n\n"
    "{\n"
    '  "leverantor": "leverantörens namn",\n'
    '  "fakturanummer": "fakturanumret",\n'
    '  "ocr": "OCR/referensnummer för betalning, tom sträng om det saknas",\n'
    '  "fakturadatum": "YYYY-MM-DD",\n'
    '  "forfallodatum": "YYYY-MM-DD",\n'
    '  "belopp_exkl_moms": 0.0,\n'
    '  "moms": 0.0,\n'
    '  "totalbelopp": 0.0\n'
    "}\n\n"
    "Om ett fält inte går att läsa, sätt det till en tom sträng (eller 0 för "
    "belopp) - gissa aldrig ett värde du inte kan se."
)


def extract_invoice(key, media_type, base64_data):
    if not check_key(key):
        return 403, {'error': 'forbidden'}

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return 503, {'error': 'ai_not_configured'}

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
        'max_tokens': 1024,
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
        with urllib.request.urlopen(req, timeout=30) as resp:
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

    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        return 502, {'error': 'ai_no_json', 'raw': text[:300]}

    try:
        fields = json.loads(match.group(0))
    except json.JSONDecodeError:
        return 502, {'error': 'ai_invalid_json', 'raw': text[:300]}

    return 200, {'ok': True, 'fields': fields}
