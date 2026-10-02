"""Evaluate own OCR on fonts excluded from prototype fitting; no semantic claims."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from printed_ocr import MODEL_PATH, recognize
from PIL import Image, ImageDraw, ImageFont
import numpy as np


def distance(a, b):
    previous = list(range(len(b)+1))
    for index, char in enumerate(a, 1):
        current = [index]
        for column, other in enumerate(b, 1):
            current.append(min(current[-1]+1, previous[column]+1, previous[column-1]+(char != other)))
        previous = current
    return previous[-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('/tmp/ia-ocr-evaluation.json'))
    args = parser.parse_args()
    fonts = [Path('/usr/share/fonts/truetype/lato/Lato-Regular.ttf'),
             Path('/usr/share/fonts/truetype/ubuntu/UbuntuSans[wdth,wght].ttf')]
    with np.load(MODEL_PATH, allow_pickle=False) as checkpoint:
        fitted = set(checkpoint['fonts'].tolist())
    if any(str(font) in fitted for font in fonts):
        raise RuntimeError('Fonte de avaliação contaminada pelo ajuste.')
    cases, errors, characters = [], 0, 0
    for font in fonts:
        for size in (20, 28, 34):
            for expected in ('42 731', 'TOTAL 128', 'Pedido 42', 'São 23', 'MEDIA 2026'):
                image = Image.new('RGB', (650,100), 'white')
                ImageDraw.Draw(image).text((15,15), expected, font=ImageFont.truetype(str(font),size), fill='black')
                result = recognize(image)
                edits = distance(expected, result['text'])
                errors += edits; characters += len(expected)
                cases.append({'font': font.name, 'size': size, 'expected': expected, 'actual': result['text'],
                              'edit_distance': edits, 'exact': expected == result['text'], 'status': result['status']})
    report = {'schema': 'own-printed-ocr-evaluation/v1', 'method': 'local-font-prototypes',
              'checkpoint_sha256': hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest(),
              'external_weights': False, 'evaluation_fonts_excluded_from_fit': True,
              'cases': cases, 'exact_cases': sum(row['exact'] for row in cases), 'total_cases': len(cases),
              'character_error_rate': round(errors/max(1, characters), 6),
              'production_eligible': False, 'scope': 'synthetic-upright-printed-text-only',
              'limitations': ['Não mede fotos, manuscritos, rotação, layouts reais ou compreensão de cenas.',
                              'Resultados experimentais exigem conferência do original.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({key: report[key] for key in ('exact_cases','total_cases','character_error_rate','production_eligible')}))


if __name__ == '__main__':
    main()
