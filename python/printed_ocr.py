"""Own, bounded OCR for upright printed glyphs; no downloaded model weights.

The prototype classifier is fitted from locally rendered font samples. It does
not understand scenes, handwriting or document layout. Ambiguous glyphs are □.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import string
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / 'model/perception/printed-ocr-v1.npz'
ALPHABET = string.ascii_letters + string.digits + '.,:;!?+-/=%()@_' + 'áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ'


def feature(mask, scale=32):
    image = Image.fromarray(np.asarray(mask, dtype=np.uint8) * 255)
    box = image.getbbox()
    if box is None:
        return np.zeros(1026, dtype=np.float32)
    image = image.crop(box)
    ratio = image.width / max(1, image.height)
    height = image.height/max(1, scale)
    factor = min(28/image.width, 28/image.height)
    image = image.resize((max(1, round(image.width*factor)), max(1, round(image.height*factor))), Image.Resampling.LANCZOS)
    canvas = Image.new('L', (32, 32))
    canvas.paste(image, ((32-image.width)//2, (32-image.height)//2))
    return np.concatenate((np.asarray(canvas, dtype=np.float32).reshape(-1)/255,
                           np.array([min(ratio, 2), height], dtype=np.float32)))


def fit(path=MODEL_PATH):
    fonts = [ROOT_PATH / name for ROOT_PATH, name in [
        (Path('/usr/share/fonts/truetype/liberation'), 'LiberationSans-Regular.ttf'),
        (Path('/usr/share/fonts/truetype/liberation'), 'LiberationMono-Regular.ttf'),
        (Path('/usr/share/fonts/truetype/liberation'), 'LiberationSans-Bold.ttf'),
        (Path('/usr/share/fonts/truetype/dejavu'), 'DejaVuSans.ttf'),
        (Path('/usr/share/fonts/truetype/dejavu'), 'DejaVuSansMono.ttf'),
    ] if (ROOT_PATH / name).is_file()]
    if not fonts:
        raise ValueError('Não há fontes locais para ajustar o OCR próprio.')
    vectors, labels = [], []
    for font_path in fonts:
        for size in (16, 22, 28, 36, 44):
            font = ImageFont.truetype(str(font_path), size)
            cap_box = font.getbbox('H')
            cap_height = max(1, cap_box[3]-cap_box[1])
            for char in ALPHABET:
                image = Image.new('L', (90, 90), 255)
                ImageDraw.Draw(image).text((12, 6), char, font=font, fill=0)
                for threshold in (120, 180):
                    vectors.append(feature(np.asarray(image) < threshold, cap_height))
                    labels.append(char)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, vectors=np.asarray(vectors), labels=np.asarray(labels),
                        fonts=np.asarray([str(item) for item in fonts]), schema='local-printed-ocr/v1')
    return {'schema': 'local-printed-ocr-fit/v1', 'path': str(path), 'samples': len(labels),
            'fonts': [item.name for item in fonts], 'method': 'local-font-prototypes',
            'external_weights': False, 'neural': False}


def runs(active):
    result, start = [], None
    for index, value in enumerate([*active, False]):
        if value and start is None:
            start = index
        if not value and start is not None:
            result.append((start, index))
            start = None
    return result


def components(mask):
    """Separate overlapping glyph columns using actual connected ink pixels."""
    height, width = mask.shape
    remaining = set((y*width+x) for y, x in zip(*np.nonzero(mask)))
    boxes = []
    while remaining:
        seed = remaining.pop()
        pending = [seed]
        y, x = divmod(seed, width)
        x0 = x1 = x
        y0 = y1 = y
        while pending:
            pixel = pending.pop()
            y, x = divmod(pixel, width)
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
                ny, nx = y+dy, x+dx
                neighbor = ny*width+nx
                if 0 <= ny < height and 0 <= nx < width and neighbor in remaining:
                    remaining.remove(neighbor)
                    pending.append(neighbor)
        boxes.append([x0, y0, x1+1, y1+1])
    # Dots and accents belong to the vertically aligned body below/above.
    boxes.sort(key=lambda box: box[3]-box[1], reverse=True)
    merged = []
    for box in boxes:
        small = box[3]-box[1] < height*.35
        target = next((other for other in merged if small
                       and min(box[2], other[2]) > max(box[0], other[0])
                       and (box[3] <= other[1] or other[3] <= box[1])), None)
        if target is None:
            merged.append(box)
        else:
            target[:] = [min(target[0], box[0]), min(target[1], box[1]),
                         max(target[2], box[2]), max(target[3], box[3])]
    return sorted(merged, key=lambda box: box[0])


def recognize(image, model_path=MODEL_PATH, max_chars=12000):
    if not Path(model_path).is_file():
        return {'status': 'unavailable', 'text': '', 'backend': 'own-printed-ocr',
                'warnings': ['O classificador próprio de OCR ainda não foi ajustado.']}
    with np.load(model_path, allow_pickle=False) as archive:
        vectors, labels = archive['vectors'], archive['labels']
    image = ImageOps.exif_transpose(image)
    if 'A' in image.getbands():
        background = Image.new('RGBA', image.size, 'white')
        image = Image.alpha_composite(background, image.convert('RGBA'))
    image = image.convert('L')
    if image.width * image.height > 12_000_000:
        return {'status': 'limited', 'text': '', 'warnings': ['Imagem acima do orçamento do OCR (12 MP).']}
    pixels = np.asarray(image)
    border = np.concatenate((pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]))
    mask = pixels < 160 if np.median(border) >= 128 else pixels > 160
    if mask.mean() > .4:
        return {'status': 'limited', 'text': '', 'warnings': ['O OCR exige texto impresso com fundo uniforme.']}
    row_runs = runs(mask.any(axis=1))
    groups = []
    merge_gap = max(2, int(np.median([end-start for start, end in row_runs] or [8]) * .3))
    for start, end in row_runs:
        if groups and start-groups[-1][1] <= merge_gap:
            groups[-1] = (groups[-1][0], end)
        else:
            groups.append((start, end))
    lines, glyphs, unknown = [], [], 0
    deadline = time.monotonic()+6
    for y0, y1 in groups[:150]:
        if y1-y0 < 5 or time.monotonic() > deadline:
            continue
        if mask[y0:y1].sum() > 200000:
            continue
        columns = components(mask[y0:y1])
        widths = [box[2]-box[0] for box in columns if box[2]-box[0] >= 3]
        space = max(3, np.median(widths or [10]) * .4)
        line, previous = '', None
        for x0, cy0, x1, cy1 in columns:
            if len(glyphs) >= min(max_chars, 1200) or time.monotonic() > deadline:
                break
            vector = feature(mask[y0+cy0:y0+cy1, x0:x1], y1-y0)
            distances = ((vectors[:, :1024]-vector[:1024])**2).mean(axis=1)
            distances += .08*(vectors[:, 1024]-vector[1024])**2
            distances += .15*(vectors[:, 1025]-vector[1025])**2
            best = int(np.argmin(distances))
            alternatives = distances[labels != labels[best]]
            margin = float(alternatives.min()-distances[best]) if alternatives.size else 0
            accepted = float(distances[best]) <= .065 and margin >= .001
            char = str(labels[best]) if accepted else '□'
            if previous is not None and x0-previous > space:
                line += ' '
            line += char
            previous = x1
            unknown += not accepted
            glyphs.append({'box': [int(x0), int(y0), int(x1), int(y1)], 'character': char,
                           'distance': round(float(distances[best]), 5), 'margin': round(margin, 5)})
        if line:
            lines.append(line)
    text = '\n'.join(lines)[:max_chars]
    warnings = ['OCR próprio experimental: texto impresso sem rotação; □ indica caractere incerto.']
    if time.monotonic() > deadline or len(glyphs) >= min(max_chars, 1200):
        warnings.append('Leitura reduzida pelo orçamento de tempo ou caracteres do OCR.')
    if not text:
        warnings.append('Não foi encontrado texto legível dentro do escopo do OCR.')
    return {'schema': 'local-printed-ocr/v1', 'backend': 'own-font-prototypes',
            'status': 'partial' if unknown or not text else 'ok', 'text': text,
            'unknown_characters': int(unknown), 'glyph_count': len(glyphs),
            'glyphs': glyphs[:100], 'warnings': warnings, 'external_weights': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fit', action='store_true')
    parser.add_argument('--image')
    args = parser.parse_args()
    if args.fit:
        print(json.dumps(fit(), ensure_ascii=False))
    elif args.image:
        with Image.open(args.image) as image:
            print(json.dumps(recognize(image), ensure_ascii=False))
