"""Local, content-addressed attachment intake with explicit perception limits."""
from __future__ import annotations

import argparse
import base64
import binascii
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import struct
import threading
import warnings
import wave

from document_reader import read_document, TEXT_EXTENSIONS, ZIP_DOCUMENT_EXTENSIONS

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 16 * 1024 * 1024
MAX_BODY_BYTES = ((MAX_BYTES + 2)//3)*4 + 4096
MAX_TEXT = 24000
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff', '.pbm', '.pgm'}
AUDIO_EXTENSIONS = {'.wav', '.mp3', '.ogg', '.flac', '.m4a'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.webm'}
DOCUMENT_EXTENSIONS = TEXT_EXTENSIONS | ZIP_DOCUMENT_EXTENSIONS | {'.pdf', '.rtf', '.doc', '.xls', '.ppt', '.eml', '.ipynb'}
_INTAKE_LOCK = threading.Lock()


def capabilities():
    try:
        from PIL import Image
        from printed_ocr import MODEL_PATH
        image_reader, own_ocr = True, MODEL_PATH.is_file()
    except ImportError:
        image_reader, own_ocr = False, False
    return {'schema': 'local-media-capabilities/v1', 'policy': 'own-models-local-tools',
            'max_file_bytes': MAX_BYTES, 'external_models': False,
            'documents': {'text_office': True, 'pdf': bool(shutil.which('pdftotext'))},
            'images': {'decode': image_reader, 'ocr': own_ocr, 'ocr_scope': 'experimental-upright-printed-text',
                       'scene_understanding': False},
            'audio': {'wav_inspection': True, 'transcription': False, 'speech_generation': False},
            'video': {'mp4_container_inspection': True, 'frame_understanding': False},
            'tables': {'csv_statistics': True}}


def table_statistics(text):
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t')
        rows = list(csv.reader(io.StringIO(text), dialect))[:1002]
    except csv.Error:
        return None
    if len(rows) < 2 or not 1 < len(rows[0]) <= 60:
        return None
    header, data = rows[0], rows[1:1001]
    columns = []
    for index, name in enumerate(header):
        values = []
        for row in data:
            cell = row[index].strip() if index < len(row) else ''
            if len(cell) > 20 or not re.fullmatch(r'[-+]?\d+(?:[.,]\d+)?', cell):
                continue
            try:
                values.append(Decimal(cell.replace(',', '.')))
            except InvalidOperation:
                continue
        if values:
            columns.append({'column': name, 'numeric_values': len(values), 'sum': str(sum(values)),
                            'min': str(min(values)), 'max': str(max(values)),
                            'mean': str((sum(values)/len(values)).quantize(Decimal('.0001')))})
    return {'rows_observed': len(data), 'headers': header, 'numeric_columns': columns,
            'truncated': len(rows) > 1001, 'number_policy': 'ponto ou vírgula decimal; sem inferir separadores de milhares'}


def _mp4_inspection(path):
    result = {'format': 'ISO-BMFF', 'decoded_frames': 0}
    with path.open('rb') as stream:
        size = path.stat().st_size
        boxes = 0
        def scan(start, end, depth=0):
            nonlocal boxes
            offset = start
            while offset + 8 <= end and boxes < 1000:
                boxes += 1
                stream.seek(offset)
                length, kind = struct.unpack('>I4s', stream.read(8))
                header = 8
                if length == 1:
                    if offset+16 > end:
                        raise ValueError('Cabeçalho MP4 incompleto.')
                    length = struct.unpack('>Q', stream.read(8))[0]
                    header = 16
                elif length == 0:
                    length = end-offset
                if length < header or offset+length > end:
                    raise ValueError('Estrutura MP4 inválida ou truncada.')
                if kind == b'mvhd':
                    data = stream.read(min(length-header, 40))
                    if len(data) >= 20 and data[0] == 0:
                        timescale, duration = struct.unpack('>II', data[12:20])
                    elif len(data) >= 32 and data[0] == 1:
                        timescale = struct.unpack('>I', data[20:24])[0]
                        duration = struct.unpack('>Q', data[24:32])[0]
                    else:
                        raise ValueError('Duração MP4 inválida.')
                    if timescale:
                        result['duration_seconds'] = round(duration/timescale, 6)
                elif kind == b'moov' and depth == 0:
                    scan(offset+header, offset+length, depth+1)
                offset += length
        scan(0, size)
    return result


def analyze(path, name=None, max_chars=MAX_TEXT):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('Arquivo ausente ou acima de 16 MiB.')
    name = name or path.name
    extension = Path(name).suffix.lower()
    result = {'schema': 'local-media-evidence/v1', 'path': name, 'bytes': path.stat().st_size,
              'extension': extension.lstrip('.'), 'mime': mimetypes.guess_type(name)[0],
              'status': 'ok', 'text': '', 'metadata': {}, 'warnings': [],
              'content_trust': 'untrusted_attachment_data'}
    if extension in DOCUMENT_EXTENSIONS:
        document = read_document(path, max_chars=max_chars)
        result.update(media_type='document', text=document.get('text', ''), status=document['status'])
        result['warnings'] = document.get('warnings', [])
        result['metadata'] = {key: value for key, value in document.items()
                              if key not in {'text', 'path', 'warnings', 'schema'}}
        if extension in {'.csv', '.tsv'}:
            result['metadata']['table'] = table_statistics(result['text'])
            if result['metadata']['table']:
                result['metadata']['table']['source_truncated'] = document.get('truncated') is True
    elif extension in IMAGE_EXTENSIONS:
        try:
            from PIL import Image, ImageOps
            from printed_ocr import recognize
        except ImportError:
            result.update(media_type='image', status='unavailable')
            result['warnings'].append('Leitor local de imagens indisponível; instale as dependências do projeto.')
        else:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(path) as original:
                    if original.width*original.height > 12_000_000:
                        raise ValueError('Imagem acima do limite de 12 megapixels.')
                    image = ImageOps.exif_transpose(original)
                    result.update(media_type='image', mime=Image.MIME.get(original.format, result['mime']))
                    result['metadata'] = {'width': image.width, 'height': image.height, 'mode': image.mode,
                                          'frames': getattr(original, 'n_frames', 1), 'decoded': True}
                    ocr = recognize(image, max_chars=max_chars)
                    result['metadata']['ocr'] = {key: value for key, value in ocr.items()
                                                 if key not in {'text', 'glyphs', 'warnings'}}
                    result['text'] = ocr.get('text', '')
                    result['status'] = ocr['status']
                    result['warnings'] = ocr.get('warnings', [])
                    result['warnings'].append('Objetos e relações da cena não foram interpretados por um modelo visual.')
    elif extension in AUDIO_EXTENSIONS:
        result.update(media_type='audio', status='partial')
        if extension == '.wav':
            try:
                with wave.open(str(path), 'rb') as audio:
                    frames, rate = audio.getnframes(), audio.getframerate()
                    raw = audio.readframes(frames)
                    if len(raw) != frames*audio.getnchannels()*audio.getsampwidth():
                        raise ValueError('Áudio WAV truncado.')
                    result['metadata'] = {'format': 'WAV', 'channels': audio.getnchannels(), 'sample_rate': rate,
                                          'sample_width': audio.getsampwidth(), 'frames': frames,
                                          'duration_seconds': round(frames/rate, 6)}
            except (wave.Error, EOFError) as error:
                raise ValueError('Não foi possível decodificar o WAV: ' + str(error)) from error
            if raw and result['metadata']['sample_width'] in {1, 2}:
                import numpy as np
                samples = ((np.frombuffer(raw, dtype='uint8').astype(float)-128)/128
                           if result['metadata']['sample_width'] == 1 else np.frombuffer(raw, dtype='<i2').astype(float)/32768)
                result['metadata'].update(peak=round(float(np.abs(samples).max()), 6),
                                          rms=round(float(np.sqrt((samples*samples).mean())), 6))
        else:
            result['warnings'].append('Não há decodificador local conectado para este formato de áudio.')
        result['metadata']['transcription'] = {'status': 'unavailable', 'text': ''}
        result['warnings'].append('Transcrição e síntese de voz aguardam modelos próprios; nenhum texto falado foi inferido.')
    elif extension in VIDEO_EXTENSIONS:
        result.update(media_type='video', status='partial')
        if extension in {'.mp4', '.mov'}:
            with path.open('rb') as stream:
                signature = stream.read(12)
            if signature[4:8] != b'ftyp':
                raise ValueError('O arquivo não tem uma assinatura MP4/MOV compatível.')
            result['metadata'] = _mp4_inspection(path)
        else:
            result['warnings'].append('Contêiner de vídeo sem leitor local conectado.')
        result['warnings'].append('Quadros, cenas e fala não foram interpretados por modelos próprios.')
    else:
        raise ValueError('Formato ainda não suportado pelo leitor local.')
    return result


def evidence_content(analysis):
    facts = {key: analysis[key] for key in ('path', 'media_type', 'bytes', 'status', 'metadata', 'warnings')}
    content = analysis.get('text') or ('[Metadados observados; conteúdo sem interpretação semântica]\n' + json.dumps(facts, ensure_ascii=False))
    return content[:32000].encode()[:60000].decode('utf-8', errors='ignore')


class AttachmentStore:
    def __init__(self, directory=None):
        self.directory = Path(directory or ROOT / '.agent-state/attachments')

    def evidence(self, asset_id):
        if not isinstance(asset_id, str) or not re.fullmatch(r'[a-f0-9]{64}', asset_id):
            raise ValueError('Identificador de anexo inválido.')
        receipt = json.loads((self.directory / asset_id / 'receipt.json').read_text(encoding='utf-8'))
        if receipt.get('asset_id') != asset_id:
            raise ValueError('Recibo de anexo inconsistente.')
        return receipt['files'][0]

    def ingest(self, body):
        if not isinstance(body, dict) or body.get('schema') != 'local-attachment-upload/v1':
            raise ValueError('Contrato de anexo inválido.')
        name, encoded = body.get('name'), body.get('data_base64')
        if not isinstance(name, str) or not name or len(name) > 180 or any(char in name for char in '/\\\0') or '..' in name:
            raise ValueError('Nome do anexo inválido; envie apenas o nome do arquivo.')
        if not isinstance(encoded, str) or len(encoded) > ((MAX_BYTES+2)//3)*4:
            raise ValueError('Anexo acima do limite de 16 MiB.')
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError('Conteúdo base64 inválido.') from error
        if not data or len(data) > MAX_BYTES:
            raise ValueError('Anexo vazio ou acima do limite de 16 MiB.')
        digest = hashlib.sha256(data).hexdigest()
        suffix = Path(name).suffix.lower()
        with _INTAKE_LOCK:
            directory = self.directory / digest
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = directory / ('source' + suffix)
            if not path.exists():
                with path.open('xb') as stream:
                    os.chmod(path, 0o600)
                    stream.write(data)
            analysis = analyze(path, name)
            content = evidence_content(analysis)
            item = {'path': name, 'content': content, 'mediaType': analysis['mime'], 'assetId': digest,
                    'kind': analysis['media_type'], 'analysisStatus': analysis['status'],
                    'bytes': len(content.encode()), 'warnings': analysis['warnings'],
                    'observation': {'status': analysis['status'], 'metadata': analysis['metadata'],
                                    'extracted_text': bool(analysis.get('text')), 'media_type': analysis['media_type']}}
            receipt = {'schema': 'local-attachment/v1', 'asset_id': digest, 'sha256': digest, 'name': name,
                       'kind': analysis['media_type'], 'raw_bytes': len(data), 'analysis': analysis,
                       'files': [item], 'bytes': item['bytes'], 'total_files': 1, 'omitted': 0,
                       'warnings': analysis['warnings'], 'failed': 0}
            temporary = directory / 'receipt.tmp'
            temporary.write_text(json.dumps(receipt, ensure_ascii=False), encoding='utf-8')
            os.chmod(temporary, 0o600)
            temporary.replace(directory / 'receipt.json')
        return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--path', required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.path), ensure_ascii=False, separators=(',', ':')))
