"""Worker HTTP local para o checkpoint próprio do projeto."""

import argparse
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from model import build_model
from tokenizer import ByteBPETokenizer


class ModelService:
    def __init__(self, checkpoint_path):
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        self.config = checkpoint["config"]
        self.model = build_model(self.config)
        self.model.load_state_dict(checkpoint["state_dict"])
        self.model.eval()
        self.tokenizer = ByteBPETokenizer.load(ROOT / "model" / "tokenizer.json")
        self.memory = []
        self.knowledge_index = None
        dataset = ROOT / "python" / "data" / "combined.jsonl"
        if dataset.exists():
            for line in dataset.read_text(encoding="utf-8").splitlines():
                trace = json.loads(line)
                messages = trace.get("messages", [])
                if len(messages) >= 2 and messages[0].get("role") == "user" and messages[1].get("role") == "assistant" and "tool_call" not in messages[1]:
                    self.memory.append((messages[0].get("content", ""), messages[1].get("content", "")))
        index_path = ROOT / "corpus" / "index" / "knowledge.json"
        if index_path.exists():
            self.knowledge_index = json.loads(index_path.read_text(encoding="utf-8"))

    @staticmethod
    def _words(text):
        return set(re.findall(r"[\wÀ-ÿ]+", text.lower()))

    @staticmethod
    def _normalize(text):
        return " ".join(re.findall(r"[\wÀ-ÿ]+", text.lower()))

    @staticmethod
    def _knowledge_tokens(text):
        return set(re.findall(r"[\wÀ-ÿ]{2,}", text.lower()))

    @staticmethod
    def _last_user(prompt):
        user_messages = re.findall(r"<\|user\|>\n(.*?)\n<\|assistant\|>", prompt, flags=re.S)
        return user_messages[-1].strip() if user_messages else ""

    @classmethod
    def _contextual_question(cls, prompt):
        user_messages = re.findall(r"<\|user\|>\n(.*?)\n<\|assistant\|>", prompt, flags=re.S)
        if not user_messages:
            return ""
        current = user_messages[-1].strip()
        if len(user_messages) < 2:
            return current
        normalized = current.lower()
        short_follow_up = len(cls._words(current)) <= 7 or normalized.startswith(("e ", "e?", "isso", "isso?", "ele ", "ela ", "como assim", "por quê", "por que"))
        if short_follow_up:
            previous = user_messages[-2].strip()
            return f"{previous}\nContinuação: {current}"
        return current

    @staticmethod
    def route_intent(question):
        text = question.lower().strip()
        if "[anexo" in text or "[arquivo:" in text or "pasta:" in text:
            return "attachment-review"
        if re.search(r"^(oi|olá|ola|e aí|eai)\b", text):
            return "conversation"
        if re.search(r"\b(hoje|agora|atual|últimas?|notícias?|preços?|cotações?|clima|horário|versão atual)\b", text):
            return "current-research"
        if any(term in text for term in ("workspace", "arquivo", "pasta", "diretório", "diretorio", "projeto local")):
            return "workspace"
        if any(term in text for term in ("pesquise", "pesquisar", "internet", "fonte", "fontes", "site", "web")):
            return "web-research"
        if "quando foi" in text or ("quando" in text and "criad" in text):
            return "knowledge"
        if any(term in text for term in ("python", "rust", "código", "codigo", "program", "função", "funcao", "erro", "algoritmo", "bug")):
            return "programming"
        if any(term in text for term in ("o que é", "o que e", "quem é", "quem foi", "explique", "diferença", "diferenca", "como funciona", "quando foi")):
            return "knowledge"
        return "unknown"

    def attachment_answer(self, question):
        if "[anexo" not in question.lower() and "[arquivo:" not in question.lower() and "pasta:" not in question.lower():
            return None
        files = re.findall(r"\[Arquivo:\s*([^\]]+)\]", question, flags=re.I)
        folder = re.findall(r"Pasta:\s*([^/\]]+)", question, flags=re.I)
        if files:
            visible_files = ", ".join(files[:6])
            suffix = " e outros arquivos" if len(files) > 6 else ""
            if re.search(r"(acha|opinião|opiniao|analise|análise|resumo|entendeu|projeto)", question.lower()):
                return f"Recebi o material{(' da pasta ' + folder[0]) if folder else ''}. Já consigo ver estes arquivos: {visible_files}{suffix}. Para dar uma opinião técnica de verdade, vou cruzar a estrutura, as dependências e os pontos de entrada do projeto; posso começar pela arquitetura, pelo fluxo principal ou pelos riscos."
            return f"Recebi o anexo e encontrei: {visible_files}{suffix}. Posso ler os arquivos relevantes, resumir o projeto ou procurar um problema específico."
        if folder:
            return f"Recebi a pasta {folder[0]}. Neste momento só tenho a identificação da pasta, não o conteúdo completo dos arquivos. Se você quiser uma análise real, selecione o projeto pelo Workspace ou anexe a pasta novamente com os arquivos disponíveis para leitura."
        return "Recebi o anexo. Posso analisá-lo, mas preciso que o conteúdo esteja disponível para leitura."

    def knowledge_answer(self, question):
        """Retorna trechos do acervo quando há evidência lexical suficiente."""
        if not self.knowledge_index:
            return None
        query_terms = self._knowledge_tokens(question)
        if not query_terms:
            return None
        scores = {}
        for term in query_terms:
            weight = self.knowledge_index.get("idf", {}).get(term, 0.0)
            for doc_id in self.knowledge_index.get("postings", {}).get(term, []):
                scores[doc_id] = scores.get(doc_id, 0.0) + weight
        if not scores:
            return None
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        best_score = ranked[0][1]
        # Um documento precisa compartilhar termos suficientes para evitar
        # transformar qualquer palavra comum em uma resposta confiante.
        document_terms = self._knowledge_tokens(self.knowledge_index["documents"][ranked[0][0]]["text"])
        matched_terms = query_terms & document_terms
        generic = {"como", "qual", "quais", "onde", "quando", "capital", "sobre", "explique", "que", "de", "é", "em", "a", "o", "e", "do", "da", "dos", "das", "um", "uma", "no", "na", "nos", "nas", "ser"}
        meaningful_match = matched_terms - generic
        title = self.knowledge_index["documents"][ranked[0][0]]["text"].split("\n", 1)[0].lower()
        # Uma única palavra temática, como "Rust", não prova que o trecho
        # responde à pergunta. Exigimos dois termos específicos ou um título
        # que identifique diretamente a entidade solicitada.
        requested_terms = query_terms - generic
        title_match = any(term in title for term in requested_terms)
        if len(meaningful_match) < 2 and not title_match:
            return None
        if "capital" in query_terms:
            requested_entity = query_terms - generic
            if requested_entity and not any(term in title for term in requested_entity):
                return None
        if best_score < 1.0 or not meaningful_match:
            return None
        excerpts = []
        for doc_id, score in ranked[:1]:
            if score < best_score * 0.55:
                continue
            document = self.knowledge_index["documents"][doc_id]
            text = re.sub(r"\s+", " ", document["text"]).strip()
            meaningful_terms = {term for term in query_terms if len(term) >= 4}
            paragraphs = [part.strip() for part in document["text"].split("\n\n") if part.strip()]
            relevant = [part for part in paragraphs if meaningful_terms & self._knowledge_tokens(part)]
            excerpt = " ".join(relevant[:3]) if relevant else text[:900]
            excerpts.append(f"{excerpt[:1200]}\n\nFonte local: {document['id']} ({document['category']})")
        return "\n\n".join(excerpts)

    def memory_answer(self, prompt):
        user_messages = re.findall(r"<\|user\|>\n(.*?)\n<\|assistant\|>", prompt, flags=re.S)
        if not user_messages:
            return None
        question = user_messages[-1].strip()
        normalized = question.lower().strip()
        if re.search(r"^(oi|olá|ola|e aí|eai)\b", normalized):
            return "Olá! Estou bem e funcionando localmente. Ainda estou aprendendo a conversar com o meu próprio modelo, mas já consigo pesquisar, ler o workspace e rastrear fontes."
        if re.search(r"(fale|fala|conte|explique).*(sobre você|sobre ti|quem é você|quem e voce)", normalized):
            return "Sou a IA Local do Zero: um assistente local com runtime Rust, ferramentas para pesquisar na internet e trabalhar no workspace, memória de conversa e um modelo próprio em evolução. Quando não tenho confiança, digo isso em vez de inventar uma resposta."
        if re.search(r"(o que você consegue|o que voce consegue|quais são suas funções|quais sao suas funcoes|o que você faz|o que voce faz)", normalized):
            return "Posso conversar, responder perguntas do meu acervo, pesquisar na internet, analisar páginas, consultar e editar o workspace, buscar no código e trabalhar com documentos locais. Também registro fontes e o andamento das operações."
        if re.search(r"^(obrigad[oa]|valeu|perfeito|beleza|ok|entendi)\b", normalized):
            return "À disposição. Pode continuar a conversa ou me passar uma tarefa concreta."
        stopwords = {"a", "o", "as", "os", "um", "uma", "e", "é", "em", "de", "do", "da", "dos", "das", "que", "como", "qual", "quais", "para", "por", "com", "sobre", "me", "se", "no", "na"}
        question_words = self._words(question) - stopwords
        ranked = []
        exact_answer = None
        for example, answer in self.memory:
            example_words = self._words(example)
            if not example_words:
                continue
            score = len(question_words & example_words) / len(question_words | example_words or {""})
            if self._normalize(question) == self._normalize(example):
                exact_answer = answer
                score = 1.0
            ranked.append((score, answer))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if exact_answer:
            return exact_answer
        if not ranked or ranked[0][0] < 0.50:
            return None
        if len(ranked) > 1 and " e " in question.lower() and ranked[1][0] >= 0.35:
            first, second = ranked[0][1], ranked[1][1]
            if first != second:
                return f"{first}\n\n{second}"
        return ranked[0][1]

    def generate(self, prompt, max_tokens=64):
        question = self._contextual_question(prompt)
        intent = self.route_intent(question) if question else "unknown"
        attached = self.attachment_answer(question)
        if attached:
            return attached, "attachment-context"
        if intent == "current-research":
            return "Essa pergunta depende de informação atual. Use a ação ‘Pesquisar na internet’ para eu consultar fontes antes de responder.", "intent-router"
        if intent == "web-research":
            return "Entendi que você quer uma pesquisa com fontes. Use ‘Pesquisar na internet’ ou escreva /pesquisar seguido da consulta.", "intent-router"
        if intent == "workspace":
            return "Entendi que isso envolve o workspace local. Use ‘Workspace’, /arquivos, /ler ou /buscar para eu consultar os arquivos autorizados.", "intent-router"
        remembered = self.memory_answer(prompt)
        if remembered:
            return remembered, "curated-memory"
        if question:
            local_knowledge = self.knowledge_answer(question)
            if local_knowledge:
                return local_knowledge, "local-knowledge"

        # A geração livre só entra depois das respostas verificadas. O modelo
        # ainda é experimental, portanto uma saída precisa passar por filtros
        # simples antes de chegar ao usuário.
        tokens = self.tokenizer.encode(prompt)
        with torch.no_grad():
            for _ in range(min(max_tokens, 128)):
                context = tokens[-self.config["context_length"] :]
                logits = self.model(torch.tensor([context]))[0, -1]
                next_token = int(torch.argmax(logits).item())
                tokens.append(next_token)
                if next_token == self.tokenizer.special_tokens["<eos>"]:
                    break
        generated = self.tokenizer.decode(tokens)
        answer = generated[len(prompt) :].replace("<eos>", "").strip()
        terms = self._knowledge_tokens(question)
        repeated = bool(re.search(r"(.{3,})\1{2,}", answer, flags=re.I))
        relevant = sum(term in answer.lower() for term in terms if len(term) >= 4)
        if len(answer) >= 12 and "<|" not in answer and "�" not in answer and not repeated and (relevant >= 1 or intent == "conversation"):
            return answer, "compact-experimental"
        return "Ainda não tenho confiança suficiente para responder isso com o modelo próprio. Estou ampliando meu corpus e meus testes antes de liberar respostas experimentais.", "quality-gate"


SERVICE = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        return

    def _send(self, status, payload):
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "model": "ia-local-zero-compact-01"})
        else:
            self._send(404, {"ok": False, "error": "rota desconhecida"})

    def do_POST(self):
        if self.path != "/generate":
            self._send(404, {"ok": False, "error": "rota desconhecida"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            started = time.monotonic()
            text, backend = SERVICE.generate(body["prompt"], body.get("max_tokens", 64))
            user_messages = re.findall(r"<\|user\|>\n(.*?)\n<\|assistant\|>", body["prompt"], flags=re.S)
            intent = SERVICE.route_intent(user_messages[-1].strip()) if user_messages else "unknown"
            self._send(200, {"ok": True, "text": text, "backend": backend, "intent": intent, "elapsed_ms": round((time.monotonic() - started) * 1000), "model": "ia-local-zero-compact-01"})
        except Exception as error:
            self._send(500, {"ok": False, "error": str(error)})


def main():
    global SERVICE
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3001)
    parser.add_argument("--checkpoint", default=str(ROOT / "model" / "checkpoints" / "compact-01.pt"))
    args = parser.parse_args()
    SERVICE = ModelService(args.checkpoint)
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
