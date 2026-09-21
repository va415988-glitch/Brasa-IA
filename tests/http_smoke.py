"""Teste de integração: requer ./start.sh e usa só dados de fixture em memória."""
import json
import os
import statistics
import time
import urllib.error
import urllib.request

BASE = os.environ.get('IA_TEST_URL', 'http://127.0.0.1:3000')

def request(path, body=None, headers=None):
    data=json.dumps(body).encode() if body is not None else None
    req=urllib.request.Request(BASE+path,data=data,headers={'Content-Type':'application/json',**(headers or {})})
    try:
        with urllib.request.urlopen(req,timeout=32) as response:
            return response.status,json.load(response)
    except urllib.error.HTTPError as error:
        return error.code,json.load(error)

fixture={'kind':'directory','name':'fixture/','omitted':2,'files':[
    {'path':'fixture/package.json','content':'{"scripts":{},"dependencies":{"express":"test"}}'},
    {'path':'fixture/server.js','content':"const app = express();\napp.get('/produtos', handler);"}
]}
messages=[{'role':'user','content':'O que acha deste projeto?','attachments':[fixture]}]
status,result=request('/api/chat',{'messages':messages,'request_id':'http-fixture'})
assert status==200 and result['ok'], result
assert result['backend']=='static-analysis' and result['free_generation'] is True
assert result['coverage']['files_read']==2 and 'server.js:2' in result['text']
status,activity=request('/api/activity?after=0')
assert any(event['operation']=='chat:http-fixture' and event['done'] for event in activity['events'])
status,error=request('/api/chat',{'messages':[{'role':'system','content':'invalid'}]})
assert status==400 and error['ok'] is False,(status,error)
status,error=request('/api/tool-call',{'tool':'list_files','arguments':{}},headers={'Origin':'https://foreign.example'})
assert status==403 and error['ok'] is False,(status,error)
status,error=request('/api/tool-call',{'tool':'does_not_exist','arguments':{},'request_id':'http-invalid'})
assert status==200 and error['ok'] is False,(status,error)
status,planned=request('/api/chat',{'messages':[{'role':'user','content':'Liste os arquivos do workspace'}],'request_id':'http-agent-plan'})
assert status==200 and planned['ok'] and planned['tool_call']['tool']=='list_files', (status,planned)
tool=planned['tool_call']
status,tool_result=request('/api/tool-call',{'tool':tool['tool'],'arguments':tool['arguments'],'request_id':'http-agent-tool'})
assert status==200 and tool_result['ok'], (status,tool_result)
structured={'tool':tool['tool'],'ok':tool_result['ok'],'data':tool_result.get('data'),'error':tool_result.get('error'),'trace_id':planned['trace_id']}
status,continued=request('/api/chat',{'messages':[
    {'role':'user','content':'Liste os arquivos do workspace'},
    {'role':'tool','tool':tool['tool'],'content':json.dumps(structured)},
], 'request_id':'http-agent-followup'})
assert status==200 and continued['ok'] and continued['trace_id']==planned['trace_id'], (status,continued)
assert continued.get('agent',{}).get('status')=='completed', continued
latencies=[]
for i in range(20):
    started=time.perf_counter()
    status,data=request('/api/chat',{'messages':messages if i%2 else [{'role':'user','content':'Olá'}],'request_id':f'http-latency-{i}'})
    latencies.append((time.perf_counter()-started)*1000)
    assert status==200 and data['ok']
    assert latencies[-1]<30000
ordered=sorted(latencies)
print('PASSOU: anexos estruturados, evidências, eventos, entrada inválida, origem externa e ferramenta desconhecida.')
print(f'20 requisições HTTP locais (saudação/análise de fixture): p50={statistics.median(latencies):.1f} ms p95={ordered[18]:.1f} ms máximo={max(latencies):.1f} ms')
print('Medições são de regras/análise estática, não de geração neural.')
