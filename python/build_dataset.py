"""Valida e resume os traces usados no primeiro modelo próprio."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "data" / "combined.jsonl"
KNOWN_TOOLS = {"search_web", "open_page", "list_sources", "cite_sources"}
REQUIRED_ROLES = {"user", "assistant", "tool"}


def validate_trace(trace, line_number):
    if not isinstance(trace, dict) or not isinstance(trace.get("messages"), list):
        raise ValueError(f"linha {line_number}: messages deve ser uma lista")
    if not trace["messages"]:
        raise ValueError(f"linha {line_number}: trace vazio")

    for message in trace["messages"]:
        if message.get("role") not in REQUIRED_ROLES:
            raise ValueError(f"linha {line_number}: role inválido")
        if "tool_call" in message:
            call = message["tool_call"]
            if call.get("name") not in KNOWN_TOOLS:
                raise ValueError(f"linha {line_number}: ferramenta desconhecida")
            if not isinstance(call.get("arguments"), dict):
                raise ValueError(f"linha {line_number}: arguments deve ser objeto")

    declared_tools = set(trace.get("tools_used", []))
    if not declared_tools.issubset(KNOWN_TOOLS):
        raise ValueError(f"linha {line_number}: tools_used contém ferramenta desconhecida")


def main():
    traces = []
    for line_number, raw_line in enumerate(DATASET.read_text().splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            trace = json.loads(raw_line)
        except json.JSONDecodeError as error:
            raise ValueError(f"linha {line_number}: JSON inválido: {error}") from error
        validate_trace(trace, line_number)
        traces.append(trace)

    tool_calls = sum(
        1
        for trace in traces
        for message in trace["messages"]
        if "tool_call" in message
    )
    cited_traces = sum(1 for trace in traces if trace.get("expected_sources"))
    print(f"traces válidos: {len(traces)}")
    print(f"chamadas de ferramenta: {tool_calls}")
    print(f"traces com fontes: {cited_traces}")
    print("dataset pronto para a próxima etapa")


if __name__ == "__main__":
    main()
