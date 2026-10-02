"""Integration fixture: real Python contracts, explicitly simulated neural output."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'python'))
from model_server import ModelService, validate_agent_plan_request

payload = json.load(sys.stdin)
messages, objective, workflow, context, cognition = validate_agent_plan_request(payload)
service = ModelService('benchmark-only', trace_path=None, cognitive_router_manifest=None)
has_page = any(m.get('role') == 'tool' and json.loads(m['content']).get('tool') == 'open_page' for m in messages)
if sys.argv[1] == 'blocked':
    generated = {'decision': 'blocked', 'text': 'Preciso do nome da biblioteca.', 'gap': 'Biblioteca não identificada',
                 'evidence_ids': [], 'tool_call': None}
elif has_page:
    generated = {'decision': 'answer', 'text': 'A biblioteca exige Node 24.', 'gap': '',
                 'evidence_ids': ['obs-1'], 'tool_call': None}
else:
    generated = {'decision': 'consult', 'text': 'Vou verificar a versão exigida.', 'gap': 'Versão mínima do Node',
                 'evidence_ids': [], 'tool_call': {'tool': 'open_page', 'arguments': {'url': 'https://example.org/docs'}}}
with patch.object(service, 'local_reply', return_value=json.dumps(generated)):
    print(json.dumps(service.reply(messages, objective=objective, cognition=cognition, request_id=payload['request_id'])))
