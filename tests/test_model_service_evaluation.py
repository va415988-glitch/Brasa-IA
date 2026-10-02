import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import torch
import json


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from model_server import ModelService
from tokenizer import ByteBPETokenizer
from conditioned_context import conditioned_prompt, generation_window, window_geometry
from proactive_implementation import compact_implementation_prompt


class StopAtEos(torch.nn.Module):
    def __init__(self, vocab_size, eos_id):
        super().__init__()
        self.vocab_size = vocab_size
        self.eos_id = eos_id

    def forward(self, input_ids):
        logits = torch.zeros((1, input_ids.shape[1], self.vocab_size))
        logits[:, :, self.eos_id] = 10.0
        return logits


class ModelServiceEvaluationTests(unittest.TestCase):
    def test_kv_rebuild_happens_at_trained_chunk_boundary(self):
        plan={'assumptions':[],'operations':[{'tool':'create_file','arguments':{
            'path':'app.py','content':'# '+''.join(chr(97+(i*7)%26) for i in range(400))+'\n'}}]}
        answer=json.dumps(plan)
        messages=[{'role':'user','content':'Gere "assumptions" e "operations".'}]
        tokenizer=ByteBPETokenizer.train([answer,conditioned_prompt(messages)],vocab_size=280)
        tokens=tokenizer.encode_fast(answer,add_eos=True);size=max(tokenizer.vocab.values())+1
        prefills=[]
        class RecordCache:
            def __init__(self):self.offset=0;self.started=False
            def logits(self):
                result=torch.full((1,size),-20.);result[0,tokens[self.offset]]=20.;return result
            def prefill_with_cache(self,inputs,cache_capacity):
                if self.started:self.offset+=1
                self.started=True;prefills.append((self.offset,inputs[0].tolist()))
                return self.logits(),None,inputs.shape[1]
            def forward_next_with_cache(self,inputs,position,caches,cache_length):
                self.offset+=1;return self.logits(),None
        service=ModelService('checkpoint-that-does-not-exist.pt',trace_path=None)
        service.local_tokenizer=tokenizer;service.local_model=RecordCache()
        service.local_config={'context_length':96,'vocab_size':size,'training_window_mode':'prompt-anchor-v1'}
        with patch.dict('os.environ',{'IA_LOCAL_REPETITION_PENALTY':'1.0','IA_LOCAL_NUM_PREDICT':''}):
            generated=service.local_reply(messages)
        self.assertEqual(json.loads(generated),plan)
        prompt=tokenizer.encode_fast(conditioned_prompt(messages));_,stride,_=window_geometry(prompt,96)
        self.assertGreater(len(prefills),2)
        self.assertEqual(prefills[1][0],stride*3)
        for offset,inputs in prefills:
            self.assertEqual(inputs,generation_window(prompt,tokens[:offset],96))

    def test_anchored_checkpoint_gets_the_same_request_as_training(self):
        messages=[{'role':'user','content':compact_implementation_prompt('Crie um painel editorial para registrar leituras.')}]
        tokenizer=ByteBPETokenizer.train([conditioned_prompt(messages)],vocab_size=300)
        vocab_size=max(tokenizer.vocab.values())+1
        captured=[]
        class RecordThenEos(StopAtEos):
            def forward(self,inputs):
                captured.append(inputs[0].tolist())
                return super().forward(inputs)
        service=ModelService('checkpoint-that-does-not-exist.pt',trace_path=None)
        service.local_tokenizer=tokenizer
        service.local_config={'context_length':128,'vocab_size':vocab_size,
                              'training_window_mode':'prompt-anchor-v1','prompt_anchor_tokens':42}
        service.local_model=RecordThenEos(vocab_size,tokenizer.special_tokens['<eos>'])
        with patch.dict('os.environ',{'IA_LOCAL_REPETITION_PENALTY':'','IA_LOCAL_NUM_PREDICT':''}):
            service.local_reply(messages)
        expected=generation_window(tokenizer.encode_fast(conditioned_prompt(messages)),[],128,42)
        self.assertEqual(captured[0],expected)
        self.assertEqual(service.last_generation['repetition_penalty'],1.0)

    def test_decoder_preserves_programming_operators_and_repeated_json_keys(self):
        plan = {'assumptions': [], 'operations': [
            {'tool': 'create_file', 'arguments': {'path': f'module_{index}.py', 'content': 'def compare(a,b): return a <= b or a > b\n'}}
            for index in range(3)]}
        answer = json.dumps(plan)
        tokenizer = ByteBPETokenizer.train([answer], vocab_size=350)
        tokens = tokenizer.encode(answer, add_eos=True)
        vocab_size = max(tokenizer.vocab.values()) + 1
        class PredictPlan(torch.nn.Module):
            def __init__(self): super().__init__(); self.offset = 0
            def forward(self, inputs):
                logits = torch.full((1, inputs.shape[1], vocab_size), -20.)
                logits[0, -1, tokens[self.offset]] = 20.
                self.offset += 1
                return logits
        service = ModelService('checkpoint-that-does-not-exist.pt', trace_path=None)
        service.local_tokenizer = tokenizer
        service.local_config = {'context_length': 512, 'vocab_size': vocab_size}
        service.local_model = PredictPlan()
        with patch.dict('os.environ', {'IA_LOCAL_NUM_PREDICT': ''}):
            generated = service.local_reply([{'role': 'user', 'content': 'Implemente em JSON com "assumptions" e "operations".'}])
        self.assertEqual(json.loads(generated), plan)
        self.assertEqual(service.last_generation['quality_gate_result'], 'accepted')

    def test_decoder_failure_preserves_diagnostic_instead_of_erasing_it(self):
        tokenizer = ByteBPETokenizer.train(['teste'], vocab_size=280)
        service = ModelService('checkpoint-that-does-not-exist.pt', trace_path=None)
        service.local_tokenizer = tokenizer
        service.local_config = {'context_length': 256, 'vocab_size': max(tokenizer.vocab.values()) + 1}
        service.local_model = lambda _: (_ for _ in ()).throw(RuntimeError('controlled decoder failure'))
        self.assertIsNone(service.local_reply([{'role': 'user', 'content': 'teste'}]))
        self.assertEqual(service.last_generation['error_type'], 'RuntimeError')
        self.assertEqual(service.last_generation['quality_gate_result'], 'error')
        self.assertGreater(service.last_generation['input_tokens'], 0)

    def test_neural_reply_does_not_fallback_when_checkpoint_is_missing(self):
        service = ModelService("checkpoint-that-does-not-exist.pt", trace_path=None)
        self.assertIsNone(service.neural_reply([{"role": "user", "content": "Explique algo."}]))
        self.assertIsNotNone(service.local_model_error)

    def test_trace_can_be_disabled_for_evaluation(self):
        with tempfile.TemporaryDirectory() as temporary:
            trace_path = Path(temporary) / "traces.jsonl"
            service = ModelService("checkpoint-that-does-not-exist.pt", trace_path=None)
            service.reply([{"role": "user", "content": "Olá"}])
            self.assertFalse(trace_path.exists())

            traced = ModelService("checkpoint-that-does-not-exist.pt", trace_path=trace_path)
            traced.reply([{"role": "user", "content": "Olá"}])
            self.assertTrue(trace_path.exists())

    def test_runtime_default_uses_2048_budget_and_allows_eos(self):
        tokenizer = ByteBPETokenizer.train(['Olá, como posso ajudar?'], vocab_size=280)
        eos_id = tokenizer.special_tokens['<eos>']
        service = ModelService('checkpoint-that-does-not-exist.pt', trace_path=None)
        service.local_tokenizer = tokenizer
        service.local_config = {
            'context_length': 256,
            'vocab_size': max(tokenizer.vocab.values()) + 1,
        }
        service.local_model = StopAtEos(service.local_config['vocab_size'], eos_id)

        with patch.dict('os.environ', {'IA_LOCAL_NUM_PREDICT': ''}):
            answer = service.local_reply([{'role': 'user', 'content': 'Olá'}], knowledge=None)

        self.assertIsNone(answer)  # EOS imediato é rejeitado como resposta curta.
        self.assertEqual(service.last_generation['max_tokens'], 2048)
        self.assertEqual(service.last_generation['generated_tokens'], 1)
        self.assertEqual(service.last_generation['stop_reason'], 'eos')
        self.assertEqual(service.last_generation['quality_reason'], 'too-short')


if __name__ == "__main__":
    unittest.main()
