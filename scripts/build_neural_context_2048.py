#!/usr/bin/env python3
"""Build a deterministic long-context SFT experiment and unseen evaluation set."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from assistant_profile import COMPACT_PROFILE
from dialogue import normalize
from tokenizer import ByteBPETokenizer


SEED = 20260926
TOKENIZER_PATH = ROOT / "model/godmode/godmode-tokenizer-v1.json"
TRAIN_PATH = ROOT / "python/data/neural_context_2048_v1.jsonl"
EXPERIMENT_DIR = ROOT / "model/training/neural-context-2048-v1"
DATA_DIR = EXPERIMENT_DIR / "data"
SAMPLE_HELDOUT_PATH = DATA_DIR / "heldout_sft.jsonl"
EVAL_PATH = DATA_DIR / "neural_eval.jsonl"
BUILD_MANIFEST_PATH = DATA_DIR / "build_manifest.json"
TARGET_CONTEXT = 2048
MIN_CONTEXT = 1200
MAX_CONTEXT = 1950


PROJECTS = ["Aurora", "Ponte", "Sabiá", "Farol", "Trilha", "Candeia", "Nascente", "Horizonte"]
DATABASES = ["SQLite", "PostgreSQL", "MariaDB", "DuckDB", "Firebird", "CockroachDB"]
CRITERIA = [
    "exportar o relatório completo em CSV sem perder acentos",
    "reabrir um rascunho depois de reiniciar o aplicativo",
    "validar todos os campos antes de confirmar uma importação",
    "manter um histórico legível de cada alteração aprovada",
    "permitir restaurar uma cópia de segurança em ambiente limpo",
    "concluir a busca local em menos de meio segundo no conjunto acordado",
]
FEATURES = ["sincronização entre filiais", "painel de notificações", "importação por planilha", "relatório por período", "modo de revisão", "atalhos de teclado"]
SERVICES = ["serviço de relatórios", "fila de notificações", "catálogo de produtos", "gateway de pagamentos", "processador de imagens", "serviço de autenticação"]
CAUSES = [
    "a rotina de compactação mantinha o bloqueio do banco durante a gravação do índice",
    "o worker encerrava a tarefa antes de confirmar o último lote recebido",
    "a renovação do certificado ocorria depois do primeiro pedido feito pelo balanceador",
    "a fila repetia mensagens sem respeitar a chave de idempotência já gravada",
    "o cache devolvia uma configuração antiga depois da troca do grupo de implantação",
    "o limite de conexões era menor que o número de tarefas concorrentes do importador",
]
RECOVERIES = [
    "doze reprocessamentos terminaram sem erro e o p95 ficou em 240 milissegundos",
    "vinte requisições consecutivas retornaram o estado esperado e nenhuma ficou pendente",
    "a fila foi drenada até zero e três verificações independentes confirmaram o resultado",
    "o teste de restauração foi repetido em ambiente limpo e todos os registros conferiram",
    "o monitor permaneceu estável por trinta minutos e não registrou nova tentativa automática",
    "dezesseis chamadas de ponta a ponta terminaram sem timeout depois da correção",
]
CONSTRAINTS = [
    "não interromper o acesso da equipe durante o expediente",
    "preservar os anexos originais e o identificador do atendimento",
    "não exigir que a pessoa instale um aplicativo adicional",
    "manter o mesmo endereço de entrega já confirmado no cadastro",
    "não apagar o histórico de mensagens nem os comprovantes enviados",
    "resolver o caso antes da próxima cobrança prevista para segunda-feira",
]
SOLUTIONS = [
    "reenvio parcial dos itens faltantes em entrega expressa",
    "crédito integral da taxa e manutenção do pedido original",
    "troca somente da peça defeituosa com coleta agendada",
    "reposição do acessório sem alterar o restante da compra",
    "estorno do frete e envio de uma unidade substituta",
    "segunda via corrigida por e-mail sem cancelar o cadastro atual",
]
EXPERIMENTS = ["Aster", "Boreal", "Cedro", "Delta", "Íris", "Jatobá", "Lótus", "Orla"]
CONFIGS = ["configuração A", "configuração B", "configuração C", "configuração D", "configuração E", "configuração F"]
LIMITATIONS = [
    "o conjunto ainda não cobre imagens com iluminação noturna",
    "a amostra não permite concluir o comportamento em aparelhos antigos",
    "o resultado não foi repetido fora do laboratório controlado",
    "o ganho desapareceu quando a sequência continha campos ausentes",
    "a medição inclui só dados de uma região e precisa de replicação",
    "o teste não avaliou o custo de memória sob carga concorrente",
]


NOTES = {
    "planning": [
        "A equipe de interface revisou rótulos, mensagens de erro e ordem de navegação; nenhuma mudança alterou o contrato de dados aprovado.",
        "O responsável pela implantação confirmou que a janela de manutenção continua reservada e que o plano de retorno está documentado.",
        "A revisão de acessibilidade encontrou dois textos de ajuda longos; a equipe os dividiu sem remover instruções necessárias.",
        "A pessoa de testes separou os casos de importação válida, arquivo vazio e caractere fora da codificação esperada.",
        "O grupo de suporte atualizou o roteiro de demonstração e marcou quais telas ainda usam dados fictícios.",
        "A revisão do repositório confirmou que arquivos exportados ficam fora do diretório temporário após o encerramento.",
        "O responsável pelo projeto pediu que os comentários de decisão mantenham data, autor e motivo da alteração.",
        "A checagem de navegação por teclado cobriu os formulários principais e registrou uma pendência no seletor de datas.",
        "O time comparou a documentação com a versão instalada e corrigiu dois nomes de campo que estavam desatualizados.",
        "O ensaio de instalação foi feito em uma máquina sem cache e terminou sem baixar componentes não declarados.",
        "A equipe de dados conferiu o formato decimal, o separador de colunas e a preservação de caracteres portugueses.",
        "A reunião de acompanhamento manteve a data da revisão e transferiu a discussão de notificações para outro ciclo.",
    ],
    "incident": [
        "O painel recebeu métricas do balanceador, da fila e do banco, mas nenhuma dessas observações isoladas confirmou uma causa.",
        "A pessoa de plantão comparou a primeira falha com duas implantações anteriores e guardou os horários em UTC.",
        "A equipe desativou temporariamente o alerta duplicado para separar a frequência de notificações da frequência de erros reais.",
        "Uma consulta de leitura permaneceu estável durante o pico, então o grupo manteve essa hipótese em observação.",
        "O plano de mitigação preservou os registros recebidos e proibiu apagar a fila antes da conferência do responsável.",
        "O monitoramento adicionou uma métrica por tentativa e outra por confirmação para expor tarefas sem encerramento.",
        "A revisão de infraestrutura registrou a versão do contêiner, a política de reinício e o limite de memória configurado.",
        "A equipe reproduziu parte do atraso em ambiente de teste, mas ainda não tinha evidência suficiente para fechar o incidente.",
        "O relatório separou sintomas observados, explicações provisórias e resultados que já tinham sido repetidos.",
        "O suporte avisou os usuários afetados sobre a investigação sem atribuir o problema a um componente ainda não confirmado.",
        "A comparação entre regiões não mostrou diferença de configuração suficiente para explicar a falha por geografia.",
        "O responsável guardou o identificador da execução e pediu nova verificação depois da correção proposta.",
    ],
    "support": [
        "A pessoa de atendimento conferiu o número do pedido, a data da compra e os anexos antes de sugerir uma solução.",
        "O cliente respondeu pelo canal original e confirmou que recebeu as orientações, mas pediu alguns minutos para decidir.",
        "A equipe explicou o prazo de cada opção e deixou claro qual etapa depende de confirmação do destinatário.",
        "O cadastro foi conferido sem alterar endereço, telefone ou preferência de contato que já estavam corretos.",
        "O supervisor revisou as opções disponíveis e registrou qual delas preserva melhor o histórico do atendimento.",
        "Uma mensagem automática foi desativada para evitar que o mesmo protocolo recebesse avisos repetidos.",
        "O grupo separou a falha do produto do atraso logístico e encaminhou cada parte ao responsável adequado.",
        "O cliente pediu que nenhuma etapa fosse marcada como concluída antes de receber o comprovante correspondente.",
        "A política aplicável foi conferida na data da compra e o resumo foi anexado à conversa.",
        "A equipe confirmou que a próxima atualização será enviada pelo mesmo canal e vinculada ao protocolo atual.",
        "O atendimento registrou uma alternativa de contingência, mas ela não foi aplicada sem autorização do cliente.",
        "A revisão final removeu dados pessoais desnecessários da nota interna e preservou somente o identificador do caso.",
    ],
    "research": [
        "A equipe guardou a semente aleatória, a versão do conjunto e o hash do código para permitir repetição do experimento.",
        "A métrica foi calculada no conjunto reservado depois da seleção e permaneceu separada da loss de treinamento.",
        "O caderno registra valores ausentes, critérios de exclusão e a quantidade de amostras em cada partição.",
        "O grupo repetiu a execução com o mesmo orçamento e comparou as variações, sem escolher pelo melhor caso isolado.",
        "A normalização foi ajustada somente com estatísticas da partição de treino para evitar vazamento entre conjuntos.",
        "A revisão anotou quais medições vieram do código e quais conclusões ainda dependem de inspeção manual.",
        "O responsável salvou os parâmetros, o tempo total e a memória usada em cada execução comparável.",
        "A equipe separou o efeito da augmentação do efeito da mudança de arquitetura antes de interpretar o resultado.",
        "O conjunto de avaliação permaneceu fechado durante a escolha e não foi consultado para ajustar hiperparâmetros.",
        "O relatório preservou exemplos de erro e acerto para facilitar uma revisão posterior com os mesmos critérios.",
        "O grupo registrou que uma métrica agregada pode esconder diferenças entre categorias pouco representadas.",
        "A validação foi repetida com uma ordem diferente de lotes e não alterou a conclusão já documentada.",
    ],
}


def key_for(row: dict) -> str:
    question = row["messages"][0]["content"]
    return " ".join(normalize(question).split()).rstrip(".?! ")


def split_bucket(row: dict) -> int:
    digest = hashlib.sha256(key_for(row).encode("utf-8")).hexdigest()[:8]
    return int(digest, 16) % 10


def dated(index: int) -> str:
    return (date(2026, 1, 5) + timedelta(days=index * 2)).isoformat()


def make_row(family: str, rng: random.Random, record_id: str, *, heldout: bool = False) -> tuple[dict, dict]:
    notes = NOTES[family]
    event_count = 34
    events = []
    for index in range(1, event_count + 1):
        detail = rng.choice(notes)
        events.append(f"Registro {index:02d} · {dated(index)}: {detail}")

    if family == "planning":
        project = rng.choice(PROJECTS)
        database = rng.choice(DATABASES)
        criterion = rng.choice(CRITERIA)
        deferred = rng.choice([item for item in FEATURES if item not in {"relatório por período", "importação por planilha"}])
        events[0] = (
            f"Registro 01 · {dated(1)}: A carta de aceite do projeto {project} determina que a entrega precisa {criterion}; "
            "a equipe deve conservar esse requisito mesmo se as prioridades de implementação mudarem."
        )
        events[-1] = (
            f"Registro {event_count:02d} · {dated(event_count)}: Decisão final da versão um de {project}: "
            f"usar {database}, manter o critério de aceite da carta inicial e deixar {deferred} para uma versão futura."
        )
        questions = [
            f"Revise a ata completa do projeto {project}. Qual banco foi aprovado para a versão um e qual critério inicial continua obrigatório?",
            f"Considerando a primeira carta de aceite e a decisão mais recente de {project}, o que ficou decidido sobre o banco e o requisito mínimo?",
            f"A equipe encerrou a revisão de {project}. Informe o armazenamento escolhido e o critério que não pode se perder na entrega.",
        ]
        question = rng.choice(questions)
        answer = f"A versão um usará {database}; continua obrigatório {criterion}."
        terms = [database, criterion.split()[-1].rstrip(".")]
        topic = project
    elif family == "incident":
        service = rng.choice(SERVICES)
        cause = rng.choice(CAUSES)
        recovery = rng.choice(RECOVERIES)
        symptom = rng.choice([
            "respostas demoravam mais de dois segundos depois do segundo lote",
            "tarefas apareciam como iniciadas sem chegar ao estado concluído",
            "pedidos intermitentes recebiam timeout durante o pico de carga",
            "o painel mostrava o resultado anterior depois de uma atualização confirmada",
        ])
        events[0] = (
            f"Registro 01 · {dated(1)}: O incidente no {service} começou quando {symptom}; "
            "essa descrição é o sintoma inicial, não uma causa confirmada."
        )
        events[-2] = (
            f"Registro {event_count-1:02d} · {dated(event_count-1)}: A reprodução isolada confirmou que a causa era {cause}; "
            "as hipóteses de rede e de relógio foram descartadas por medições repetidas."
        )
        events[-1] = (
            f"Registro {event_count:02d} · {dated(event_count)}: Após a correção no {service}, a verificação final mostrou que {recovery}; "
            "o responsável encerrou a mitigação e manteve o monitoramento ativo."
        )
        questions = [
            f"Leia toda a linha do tempo do {service}. Qual causa foi confirmada e que resultado observável verificou a recuperação?",
            f"Com base no diagnóstico final do incidente do {service}, informe a causa real e a evidência de que a correção funcionou.",
            f"O relatório distingue suspeitas de confirmação. O que causou a falha do {service} e qual foi a checagem final?",
        ]
        question = rng.choice(questions)
        answer = f"A causa confirmada foi {cause}. A recuperação foi verificada porque {recovery}."
        terms = [cause.split()[1], recovery.split()[1]]
        topic = service
    elif family == "support":
        customer = rng.choice(["Rita", "Caio", "Marta", "Davi", "Lúcia", "Enzo", "Nina", "Ivo"])
        constraint = rng.choice(CONSTRAINTS)
        solution = rng.choice(SOLUTIONS)
        ticket = f"AT-{rng.randint(1200, 9800)}"
        events[0] = (
            f"Registro 01 · {dated(1)}: No atendimento {ticket}, {customer} explicou que precisava resolver a compra e pediu para {constraint}; "
            "essa restrição deve ser respeitada em qualquer proposta."
        )
        events[-1] = (
            f"Registro {event_count:02d} · {dated(event_count)}: Depois de comparar as opções, {customer} aceitou {solution}; "
            f"a equipe confirmou que o plano mantém a condição de {constraint}."
        )
        questions = [
            f"Resuma a decisão final do atendimento {ticket}: qual solução {customer} aceitou e qual condição precisa ser preservada?",
            f"No caso {ticket}, qual alternativa foi escolhida pela pessoa e que restrição original continuou valendo?",
            f"Considere o histórico inteiro de {ticket}, inclusive a última confirmação. O que foi aceito e o que não pode ser alterado?",
        ]
        question = rng.choice(questions)
        answer = f"{customer} aceitou {solution}; a equipe deve {constraint}."
        constraint_term = next(
            word for word in constraint.split()
            if len(word.strip(".,;:")) >= 6 and word.casefold() not in {"preservar", "manter"}
        )
        terms = [solution.split()[0], constraint_term.strip(".,;:")]
        topic = ticket
    else:
        experiment = rng.choice(EXPERIMENTS)
        config = rng.choice(CONFIGS)
        metric = rng.choice(["F1 de 0,84", "recall de 0,91", "erro absoluto de 0,12", "acurácia de 93 por cento", "p95 de 180 milissegundos"])
        limitation = rng.choice(LIMITATIONS)
        objective = rng.choice([
            "classificar solicitações curtas sem misturar as categorias raras",
            "detectar campos ausentes sem rejeitar registros válidos",
            "reduzir a latência sem perder a cobertura das classes menores",
            "comparar estabilidade entre duas partições mantidas fora do treino",
        ])
        events[0] = (
            f"Registro 01 · {dated(1)}: O experimento {experiment} foi planejado para {objective}; "
            "a métrica principal deve ser comparada somente no conjunto reservado."
        )
        events[-2] = (
            f"Registro {event_count-1:02d} · {dated(event_count-1)}: A seleção final do experimento {experiment} favoreceu {config}, "
            f"que alcançou {metric} no conjunto reservado e superou o baseline definido antes da execução."
        )
        events[-1] = (
            f"Registro {event_count:02d} · {dated(event_count)}: A conclusão do experimento {experiment} conserva uma ressalva: {limitation}; "
            "a equipe não deve generalizar o resultado além da população medida."
        )
        questions = [
            f"Leia todas as notas do experimento {experiment}. Qual configuração foi escolhida e qual limitação precisa acompanhar o resultado?",
            f"Considerando o resultado reservado e a ressalva final de {experiment}, informe a configuração vencedora e o limite da conclusão.",
            f"O que o relatório final de {experiment} recomenda: qual configuração venceu e que conclusão ainda não está autorizada?",
        ]
        question = rng.choice(questions)
        answer = f"Foi escolhida a {config}, com {metric}; a conclusão permanece limitada porque {limitation}."
        terms = [config, limitation.split()[1]]
        topic = experiment

    # Randomize only the middle notes. The initial constraint and final update
    # remain far apart, so the answer depends on reading the whole case history.
    middle = events[1:-2] if family in {"incident", "research"} else events[1:-1]
    rng.shuffle(middle)
    if family in {"incident", "research"}:
        events = [events[0], *middle, events[-2], events[-1]]
    else:
        events = [events[0], *middle, events[-1]]

    title = {
        "planning": "ATA DE ACOMPANHAMENTO DO PROJETO",
        "incident": "LINHA DO TEMPO DO INCIDENTE",
        "support": "HISTÓRICO DO ATENDIMENTO",
        "research": "CADERNO DE EXPERIMENTO",
    }[family]
    prompt = (
        f"{title}\n\n"
        f"Referência: {topic}. Leia os registros em ordem cronológica. As notas intermediárias incluem tarefas paralelas, "
        "hipóteses e verificações que não substituem uma decisão confirmada. Use somente os fatos registrados. "
        "Se uma conclusão tiver uma ressalva explícita, preserve-a na resposta.\n\n"
        + "\n".join(events)
        + f"\n\nPergunta: {question}"
    )
    row = {
        "id": record_id,
        "domain": f"long-context-{family}-2048-v1",
        "provenance": "synthetic-authored-context-retrieval-v1",
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": answer},
        ],
    }
    eval_case = {"id": record_id, "prompt": prompt, "terms": terms, "reference_answer": answer}
    return row, eval_case


def full_training_length(tokenizer: ByteBPETokenizer, row: dict) -> int:
    prompt = row["messages"][0]["content"]
    answer = row["messages"][1]["content"]
    prefix = f"<|system|>\n{COMPACT_PROFILE}\n<|user|>\n{prompt}\n<|assistant|>\n"
    return len(tokenizer.encode_fast(prefix)) + len(tokenizer.encode_fast(answer + "\n")) + 1


def main() -> int:
    output_paths = [TRAIN_PATH, SAMPLE_HELDOUT_PATH, EVAL_PATH, BUILD_MANIFEST_PATH]
    if EXPERIMENT_DIR.exists() or any(path.exists() for path in output_paths):
        raise FileExistsError(f"saídas do experimento já existem; não serão sobrescritas: {EXPERIMENT_DIR}")
    tokenizer = ByteBPETokenizer.load(TOKENIZER_PATH)
    family_counts = {}
    train_rows = []
    training_validation_rows = []
    sample_heldout_rows = []
    sample_eval_rows = []
    neural_eval_rows = []
    neural_eval_lengths = []

    for family_index, family in enumerate(NOTES):
        selected = {1: [], 0: []}
        attempt = 0
        while len(selected[1]) < 24 or len(selected[0]) < 4:
            attempt += 1
            rng = random.Random(SEED + family_index * 100_000 + attempt)
            row, _ = make_row(family, rng, f"lc2048-{family}-pool-{attempt:04d}")
            bucket = split_bucket(row)
            if bucket == 0 and len(selected[0]) < 4:
                selected[0].append(row)
            elif bucket != 0 and len(selected[1]) < 24:
                selected[1].append(row)
            if attempt > 100_000:
                raise RuntimeError(f"não consegui preencher os grupos hash de {family}")
        for index, row in enumerate(selected[1], start=1):
            row["id"] = f"lc2048-{family}-train-{index:03d}"
            train_rows.append(row)
        for index, row in enumerate(selected[0], start=1):
            row["id"] = f"lc2048-{family}-validation-{index:02d}"
            training_validation_rows.append(row)
        family_counts[family] = {"train": len(selected[1]), "validation_by_hash": len(selected[0])}

        for index in range(1, 3):
            rng = random.Random(SEED + 1_000_000 + family_index * 10_000 + index)
            row, eval_case = make_row(family, rng, f"lc2048-{family}-heldout-{index:02d}", heldout=True)
            sample_heldout_rows.append(row)
            sample_eval_rows.append(eval_case)
        for index in range(1, 7):
            rng = random.Random(SEED + 2_000_000 + family_index * 10_000 + index)
            eval_row, eval_case = make_row(family, rng, f"lc2048-{family}-eval-{index:02d}", heldout=True)
            neural_eval_rows.append(eval_case)
            neural_eval_lengths.append(full_training_length(tokenizer, eval_row))

    long_rows = train_rows + training_validation_rows
    lengths = [full_training_length(tokenizer, row) for row in long_rows + sample_heldout_rows]
    lengths.extend(neural_eval_lengths)
    if min(lengths) < MIN_CONTEXT or max(lengths) > MAX_CONTEXT:
        raise ValueError(
            f"sequências devem ficar em {MIN_CONTEXT}..{MAX_CONTEXT} tokens antes do EOS; "
            f"observado min={min(lengths)} max={max(lengths)}. Ajuste event_count."
        )
    for index, row in enumerate(long_rows + sample_heldout_rows):
        row["training_sequence_tokens_2048"] = lengths[index]

    EXPERIMENT_DIR.mkdir(parents=True)
    DATA_DIR.mkdir()
    TRAIN_PATH.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in long_rows), encoding="utf-8")
    SAMPLE_HELDOUT_PATH.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in sample_heldout_rows), encoding="utf-8")
    EVAL_PATH.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in neural_eval_rows), encoding="utf-8")
    manifest = {
        "schema": "neural-long-context-curriculum/v1",
        "seed": SEED,
        "tokenizer": str(TOKENIZER_PATH.relative_to(ROOT)),
        "tokenizer_vocab_entries": len(tokenizer.vocab),
        "target_training_context": TARGET_CONTEXT,
        "sequence_length_includes_profile_and_answer": True,
        "sequence_length_policy": {"min": MIN_CONTEXT, "max": MAX_CONTEXT},
        "training_rows": len(train_rows),
        "validation_rows_selected_by_training_split_hash": len(training_validation_rows),
        "small_post_selection_heldout_rows": len(sample_heldout_rows),
        "direct_neural_eval_rows": len(neural_eval_rows),
        "families": family_counts,
        "lengths_tokens": {
            "min": min(lengths),
            "median": sorted(lengths)[len(lengths) // 2],
            "p95": sorted(lengths)[int((len(lengths) - 1) * 0.95)],
            "max": max(lengths),
        },
        "policy": "authored synthetic examples; no external model or private user data",
        "split_rule": "training script selects rows with normalized-question SHA-256 bucket 1..9; bucket 0 is reserved for validation",
    }
    BUILD_MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
