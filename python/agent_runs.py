"""Persistent, bounded tool loop. Tool results come only from the runtime."""
import copy
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen


TERMINAL = {'completed', 'blocked', 'failed', 'cancelled', 'interrupted'}
WORKSPACE_WRITES = {'create_file', 'edit_file', 'create_web_page', 'create_directory', 'apply_repair', 'apply_batch'}


def execute_runtime(call, workspace):
    arguments = {**call['arguments'], '_expected_workspace': workspace}
    payload = {'tool': call['tool'], 'arguments': arguments, 'request_id': call['id']}
    request = Request('http://127.0.0.1:3000/api/tool-call',
                      data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    # An ambiguous timeout is never retried: a write may already have happened.
    with urlopen(request, timeout=180) as response:
        return json.load(response)


class RunEngine:
    def __init__(self, path, planner, registry, executor=execute_runtime):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.lock = threading.RLock()
        self.planner_lock = threading.Lock()
        self.planner, self.registry, self.executor = planner, registry, executor
        with self.lock:
            for run_id, body in self.db.execute('SELECT id, body FROM runs').fetchall():
                run = json.loads(body)
                if run['status'] not in TERMINAL and run['status'] != 'awaiting_approval':
                    run['uncertain_call'] = run['status'] == 'executing'
                    self._event(run, 'interrupted', 'Servidor reiniciado; execução interrompida. Confira as evidências antes de retomar.')
                    self._save(run)

    def _save(self, run):
        self.db.execute('INSERT OR REPLACE INTO runs VALUES (?, ?)', (run['id'], json.dumps(run, ensure_ascii=False)))
        self.db.commit()

    def _get(self, run_id):
        row = self.db.execute('SELECT body FROM runs WHERE id=?', (run_id,)).fetchone()
        if not row:
            raise ValueError('Tarefa não encontrada')
        return json.loads(row[0])

    def snapshot(self, run_id):
        with self.lock:
            run = self._get(run_id)
            # Stored context remains on server; observations are exposed as events.
            return {key: value for key, value in run.items() if key != 'messages'}

    def _event(self, run, status, text, **details):
        run['status'] = status
        run['updated_at'] = time.time()
        run['events'].append({'seq': len(run['events']) + 1, 'status': status,
                              'text': text, 'time': run['updated_at'], **details})

    def start(self, conversation_id, workspace, messages, request_id):
        if not isinstance(conversation_id, str) or not 1 <= len(conversation_id) <= 160:
            raise ValueError('Conversa inválida')
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 160:
            raise ValueError('Identificador de pedido inválido')
        if not isinstance(workspace, str) or not Path(workspace).is_absolute() or not Path(workspace).is_dir():
            raise ValueError('Selecione um workspace existente')
        workspace = str(Path(workspace).resolve())
        if not isinstance(messages, list) or not 1 <= len(messages) <= 80:
            raise ValueError('Envie entre 1 e 80 mensagens')
        if any(not isinstance(m, dict) or m.get('role') not in {'user', 'assistant'} or not isinstance(m.get('content'), str) for m in messages):
            raise ValueError('Contexto inválido; observações de ferramentas são produzidas pelo servidor')
        if messages[-1]['role'] != 'user' or len(json.dumps(messages)) > 2 * 1024 * 1024:
            raise ValueError('Pedido inválido ou contexto acima de 2 MiB')
        digest = hashlib.sha256(json.dumps([workspace, messages], sort_keys=True).encode()).hexdigest()
        with self.lock:
            previous_runs = [json.loads(body) for (body,) in self.db.execute('SELECT body FROM runs')]
            for previous in previous_runs:
                if previous['conversation_id'] == conversation_id and previous['request_id'] == request_id:
                    if previous.get('request_digest') != digest:
                        raise ValueError('O identificador já foi usado com outro pedido')
                    return self.snapshot(previous['id'])
            for previous in previous_runs:
                if previous['status'] not in TERMINAL and (previous['conversation_id'] == conversation_id or previous['workspace'] == workspace):
                    raise ValueError('Já há uma tarefa ativa nesta conversa ou workspace')
            run = {'id': 'run-' + uuid.uuid4().hex, 'conversation_id': conversation_id,
                   'request_id': request_id, 'request_digest': digest, 'workspace': workspace, 'messages': copy.deepcopy(messages),
                   'objective': messages[-1]['content'], 'steps': 0, 'max_steps': 12,
                   'events': [], 'pending': None, 'queue': [], 'seen': [], 'cancel_requested': False}
            self._event(run, 'planning', 'Preparando o plano da tarefa.')
            self._save(run)
            self._spawn(run['id'])
            return self.snapshot(run['id'])

    def _spawn(self, run_id):
        threading.Thread(target=self._drive, args=(run_id,), daemon=True).start()

    def control(self, run_id, action, call_id=None):
        with self.lock:
            run = self._get(run_id)
            if action == 'cancel':
                if run['status'] in TERMINAL:
                    return self.snapshot(run_id)
                run['cancel_requested'] = True
                if run['status'] in {'planning', 'executing'}:
                    self._event(run, run['status'], 'Cancelamento solicitado; aguardando a operação em andamento para registrar o resultado.')
                else:
                    self._event(run, 'cancelled', 'Tarefa cancelada antes da próxima ferramenta.')
                self._save(run)
            elif action == 'approve':
                if run['status'] != 'awaiting_approval' or not run['pending'] or run['pending']['id'] != call_id:
                    raise ValueError('A aprovação não corresponde à chamada pendente')
                run['pending']['approved'] = True
                self._event(run, 'ready', 'Alteração autorizada para esta chamada.')
                self._save(run)
                self._spawn(run_id)
            elif action == 'resume':
                if run['status'] != 'interrupted' or run.get('uncertain_call'):
                    raise ValueError('Retomada indisponível: confirme manualmente o resultado de uma chamada sem observação')
                for (body,) in self.db.execute('SELECT body FROM runs'):
                    other = json.loads(body)
                    if other['id'] != run_id and other['status'] not in TERMINAL and (other['workspace'] == run['workspace'] or other['conversation_id'] == run['conversation_id']):
                        raise ValueError('Já há outra tarefa ativa')
                self._event(run, 'planning', 'Retomando a partir das observações persistidas.')
                self._save(run)
                self._spawn(run_id)
            else:
                raise ValueError('Ação desconhecida')
            return self.snapshot(run_id)

    def _drive(self, run_id):
        try:
            while True:
                with self.lock:
                    run = self._get(run_id)
                    if run['status'] in TERMINAL:
                        return
                    if run['cancel_requested']:
                        self._event(run, 'cancelled', 'Tarefa cancelada; resultados anteriores preservados.')
                        self._save(run)
                        return
                    if run['steps'] >= run['max_steps']:
                        self._event(run, 'blocked', 'Orçamento de ferramentas atingido; tarefa pendente.')
                        self._save(run)
                        return
                    pending = run['pending']
                    messages = run['messages']
                if not pending:
                    with self.planner_lock:
                        decision = self.planner(messages)
                    with self.lock:
                        run = self._get(run_id)
                        if run['cancel_requested']:
                            self._event(run, 'cancelled', 'Planejamento cancelado antes da execução.')
                            self._save(run)
                            return
                        calls = decision.get('tool_calls') or ([decision['tool_call']] if decision.get('tool_call') else [])
                        run['trace_id'] = decision.get('trace_id') or run.get('trace_id')
                        if not calls:
                            status = (decision.get('agent') or {}).get('status')
                            status = status if status in TERMINAL else ('blocked' if decision.get('backend') == 'quality-gate' else 'completed')
                            if run.get('needs_verification') or run.get('last_tool_failed'):
                                status = 'blocked'
                                decision = {**decision, 'text': 'A tarefa permanece pendente: há falha de ferramenta ou alteração sem verificação aprovada.'}
                            self._event(run, status, decision.get('text') or 'Sem conclusão verificável.', response=decision)
                            self._save(run)
                            return
                        if not isinstance(calls, list) or len(calls) > run['max_steps'] - run['steps']:
                            raise ValueError('Plano excede o orçamento de ferramentas')
                        for call in calls:
                            valid, error = self.registry.validate_arguments(call.get('tool'), call.get('arguments'))
                            if not valid:
                                raise ValueError(error)
                            if call['tool'] in {'set_workspace', 'create_workspace'}:
                                raise ValueError('Troque o workspace pelo seletor antes de iniciar outra tarefa')
                            # Executor owns identity and approval, never the planner.
                            call['id'] = 'call-' + uuid.uuid4().hex
                            call['approved'] = False
                        run['pending'], run['queue'] = calls[0], calls[1:]
                        self._event(run, 'ready', decision.get('text') or 'Plano preparado.', plan=[{'id': c['id'], 'tool': c['tool'], 'path': c['arguments'].get('path')} for c in calls])
                        self._save(run)
                with self.lock:
                    run = self._get(run_id)
                    if run['cancel_requested']:
                        continue
                    call = run['pending']
                    contract = self.registry.describe(call['tool'])
                    mutates = contract['side_effects'] and not (call['tool'] == 'research_web' and not call['arguments'].get('save_to_corpus'))
                    fingerprint = json.dumps([call['tool'], call['arguments']], sort_keys=True)
                    # Checks/read calls may repeat after a change; writes never replay.
                    signature = fingerprint if mutates else fingerprint + ':' + str(run.get('revision', 0))
                    if signature in run['seen']:
                        self._event(run, 'blocked', 'Chamada repetida sem alteração de contexto; revise o plano.')
                        self._save(run)
                        return
                    if (mutates or contract['requires_approval']) and not call['approved']:
                        self._event(run, 'awaiting_approval', 'Revise e autorize a alteração proposta.', call=call)
                        self._save(run)
                        return
                    self._event(run, 'executing', 'Executando ' + call['tool'], call=call)
                    self._save(run)
                result = self.executor(call, run['workspace'])
                if not isinstance(result, dict) or not isinstance(result.get('ok'), bool):
                    raise ValueError('Executor retornou resultado inválido')
                with self.lock:
                    run = self._get(run_id)
                    run['steps'] += 1
                    run['last_tool_failed'] = not result['ok']
                    run['seen'].append(signature)
                    if call['tool'] in WORKSPACE_WRITES and result['ok']:
                        run['revision'] = run.get('revision', 0) + 1
                        run['needs_verification'] = True
                    observation = {**result, 'tool': call['tool'], 'call_id': call['id'], 'trace_id': run.get('trace_id')}
                    run['messages'].append({'role': 'tool', 'content': json.dumps(observation, ensure_ascii=False)})
                    self._event(run, 'observing', 'Resultado registrado: ' + call['tool'], result=observation)
                    payload = result.get('data') or {}
                    if call['tool'] == 'project_checks' and result['ok']:
                        if payload.get('passed') is True and payload.get('executed') is True:
                            run['needs_verification'] = False
                        else:
                            run['queue'] = []
                            run['pending'] = None
                            self._event(run, 'blocked', 'Verificação sem aprovação; consulte o resultado antes de continuar.')
                            self._save(run)
                            return
                    if call['tool'] == 'research_web' and result['ok'] and not any(isinstance(page, dict) and str(page.get('text') or '').strip() for page in payload.get('pages', [])):
                        run['queue'] = []
                        run['pending'] = None
                        self._event(run, 'blocked', 'Pesquisa sem conteúdo verificável; tarefa pendente.')
                        self._save(run)
                        return
                    run['pending'] = run['queue'].pop(0) if run['queue'] and result['ok'] else None
                    if not result['ok']:
                        run['queue'] = []
                    # Verify a batch only after every planned write has been observed.
                    if not run['pending'] and run.get('needs_verification') and result['ok']:
                        run['pending'] = {'id': 'call-' + uuid.uuid4().hex, 'tool': 'project_checks', 'arguments': {'check': 'auto'}, 'approved': False}
                    self._save(run)
        except Exception as error:
            with self.lock:
                run = self._get(run_id)
                uncertain = run['status'] == 'executing'
                run['uncertain_call'] = uncertain
                self._event(run, 'interrupted' if uncertain else 'failed', str(error))
                self._save(run)
