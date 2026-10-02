import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from assistant_profile import local_system_prompt
from context_policy import runtime_context_window
from conversation_compaction import compact_conversation
from tokenizer import ByteBPETokenizer


class ContextCompactionTests(unittest.TestCase):
    def test_pedido_recente_preserva_quebras_e_codigo_literal(self):
        content = 'Crie dois arquivos:\n```python\ndef answer():\n    return 42\n```'
        result = compact_conversation(
            [{"role": "user", "content": content}],
            max_input_tokens=256,
        )
        self.assertEqual(result["prompt"], f'<|user|>\n{content}\n<|assistant|>\n')

    def test_compacta_sessao_longa_e_preserva_objetivo_e_pedido_recente(self):
        messages = [
            {"role": "user", "content": "Meu objetivo é criar uma API local de pesquisa sem depender de serviços externos."},
            {"role": "assistant", "content": "O objetivo da sessão está registrado; vamos manter pesquisa local e rastreável."},
            {"role": "user", "content": "A restrição é não ultrapassar 10 GB de memória durante o teste."},
        ]
        for index in range(24):
            messages.extend([
                {"role": "user", "content": f"Atualização de implementação {index}: conferir roteamento local e documentação."},
                {"role": "assistant", "content": f"Resultado da etapa {index}: o módulo está organizado e a revisão segue em andamento."},
            ])
        messages.append({"role": "user", "content": "Agora teste a compactação e confirme o limite de contexto."})

        result = compact_conversation(messages, max_input_tokens=1200, recent_message_limit=4)

        self.assertTrue(result["stats"]["bounded"])
        self.assertLessEqual(result["stats"]["prompt_token_upper_bound"], 1200)
        self.assertGreater(result["stats"]["source_token_upper_bound"], 1200)
        self.assertEqual(result["stats"]["source_context_multiplier"], 2)
        self.assertEqual(result["stats"]["effective_source_history_tokens"], 2400)
        self.assertLessEqual(len(result["recent_messages"]), 4)
        self.assertIn("Agora teste a compactação", result["recent_messages"][-1]["content"])
        self.assertIn("objetivo", result["summary"].casefold())
        self.assertIn("resumo da conversa", result["prompt"].casefold())

    def test_janela_dobrada_ainda_reserva_memoria_antiga_apos_perfil_do_sistema(self):
        window = runtime_context_window({"context_length": 16384, "training_context_length": 512})
        context_tokens = window["runtime_context_tokens"]
        output_reserve = max(96, context_tokens // 4)
        prefix = f"<|system|>\n{local_system_prompt(context_tokens)}\n"
        messages = [{"role": "user", "content": "Meu objetivo é manter pesquisa local e preservar decisões antigas."}]
        for index in range(24):
            messages.extend([
                {"role": "assistant", "content": f"Resultado técnico {index}: memória do agente local."},
                {"role": "user", "content": f"Atualização da sessão {index}: manter contexto verificável."},
            ])
        messages.append({"role": "user", "content": "Implemente a próxima etapa em Python."})

        result = compact_conversation(
            messages,
            fixed_prefix=prefix,
            query=messages[-1]["content"],
            max_input_tokens=context_tokens - output_reserve,
            source_context_multiplier=2,
        )

        self.assertTrue(result["stats"]["bounded"])
        self.assertIn("Implemente a próxima etapa", result["prompt"])
        self.assertLessEqual(result["stats"]["prompt_token_upper_bound"] + output_reserve, context_tokens)

    def test_tokenizer_real_confirma_janela_32768_e_recupera_objetivo_antigo(self):
        root = Path(__file__).resolve().parents[1]
        metadata = json.loads((root / "model" / "godmode" / "training-neural-v1" / "candidate.safetensors.json").read_text())
        config = metadata["config"]
        window = runtime_context_window(config)
        tokenizer = ByteBPETokenizer.load(root / config["tokenizer_path"])
        prefix = f"<|system|>\n{local_system_prompt(window['runtime_context_tokens'])}\n"
        messages = [{"role": "user", "content": "Meu objetivo é manter pesquisa local e preservar decisões antigas."}]
        for index in range(80):
            messages.extend([
                {"role": "assistant", "content": f"Resultado técnico {index}: contexto e memória local verificados."},
                {"role": "user", "content": f"Atualização da sessão {index}: manter o agente local."},
            ])
        messages.append({"role": "user", "content": "Implemente a próxima etapa em Python e explique o contexto."})
        output_reserve = max(96, window["runtime_context_tokens"] // 4)

        result = compact_conversation(
            messages,
            tokenizer,
            fixed_prefix=prefix,
            query=messages[-1]["content"],
            max_input_tokens=window["runtime_context_tokens"] - output_reserve,
        )
        actual_tokens = len(tokenizer.encode(result["prompt"]))

        self.assertEqual(window["runtime_context_tokens"], 32768)
        self.assertTrue(result["stats"]["bounded"])
        self.assertEqual(result["stats"]["token_count_mode"], "bpe-exact")
        self.assertLessEqual(actual_tokens + output_reserve, 32768)
        self.assertIn("objetivo", result["summary"].casefold())
        self.assertIn("Implemente a próxima etapa", result["prompt"])

    def test_prompt_final_cabe_no_orcamento_e_preserva_unicode(self):
        messages = [
            {"role": "user", "content": "Requisito: manter português e memória útil! " + "áéíóú memória " * 100},
            {"role": "assistant", "content": "Vou preservar o pedido atual sem exceder a janela."},
            {"role": "user", "content": "Qual é o próximo passo?"},
        ]
        result = compact_conversation(
            messages,
            fixed_prefix="<|system|>\nperfil local\n",
            max_input_tokens=400,
            recent_message_limit=2,
        )
        self.assertTrue(result["stats"]["bounded"])
        self.assertLessEqual(result["stats"]["prompt_token_upper_bound"], 400)
        self.assertIn("Qual é o próximo passo?", result["prompt"])
        self.assertGreater(result["stats"]["summary_items"], 0)

    def test_historico_longo_anterior_e_compactado_sem_descartar_pergunta_atual(self):
        root = Path(__file__).resolve().parents[1]
        tokenizer = ByteBPETokenizer.load(root / "model/godmode/godmode-tokenizer-v1.json")
        history = "Fato do início: código de referência 483917. " + "registro_neutro_5821 " * 500
        question = "Qual código de referência foi registrado no histórico anterior?"
        result = compact_conversation(
            [
                {"role": "user", "content": history},
                {"role": "user", "content": question},
            ],
            tokenizer,
            query=question,
            max_input_tokens=256,
        )
        self.assertTrue(result["stats"]["bounded"])
        self.assertLessEqual(len(tokenizer.encode_fast(result["prompt"])), 256)
        self.assertEqual(len(result["recent_messages"]), 2)
        self.assertIn("483917", result["recent_messages"][0]["content"])
        self.assertIn(question, result["recent_messages"][-1]["content"])

    def test_perfil_maior_que_a_janela_nao_descarta_a_pergunta_atual(self):
        root = Path(__file__).resolve().parents[1]
        tokenizer = ByteBPETokenizer.load(root / "model" / "tokenizer.json")
        question = "O que é uma variável em Python?"
        prefix = f"<|system|>\n{local_system_prompt(256)}\n"

        self.assertGreater(len(tokenizer.encode(prefix)), 160)
        result = compact_conversation(
            [{"role": "user", "content": question}],
            tokenizer,
            fixed_prefix=prefix,
            query=question,
            max_input_tokens=160,
        )

        self.assertTrue(result["stats"]["bounded"])
        self.assertLessEqual(result["stats"]["prompt_token_upper_bound"], 160)
        self.assertIn(question, result["prompt"])
        self.assertEqual(result["recent_messages"][-1]["content"], question)


if __name__ == "__main__":
    unittest.main()
