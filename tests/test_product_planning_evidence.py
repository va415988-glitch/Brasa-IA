"""An absent source cannot satisfy the live regression's provenance check."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
sys.path.insert(0, str(ROOT / 'scripts'))
from product_planning import build_product_brief
from check_product_planning import literal_provenance


def test_literal_provenance_requires_an_actual_matching_human_source():
    case = {'prompt': 'Planeje um app para professores registrarem aulas.'}
    brief = build_product_brief(case['prompt'])
    assert literal_provenance(brief, case)
    brief['requirements'].append({'text': 'inventado', 'source_turn': 999,
                                 'evidence': {'start': 0, 'end': 9}})
    assert not literal_provenance(brief, case)


def test_goals_and_audience_are_verified_in_addition_to_requirement_lists():
    case = {'prompt': 'Planeje um app para professores registrarem aulas.'}
    brief = build_product_brief(case['prompt'])
    brief['audience']['text'] = 'banqueiros'
    assert not literal_provenance(brief, case)
    brief = build_product_brief(case['prompt'])
    brief['sources'] = []
    assert not literal_provenance(brief, case)


def test_offsets_must_be_integers_and_bound_to_the_original_turn():
    case = {'prompt': 'Planeje um app para professores registrarem aulas.'}
    for start in (True, -1, 500, '0'):
        brief = build_product_brief(case['prompt'])
        brief['goal']['evidence']['start'] = start
        assert not literal_provenance(brief, case)
