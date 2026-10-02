"""Exercise local attachment intake and the real agent, including canonical asset evidence."""
import argparse
import base64
import io
import json
from pathlib import Path
import struct
import sys
import urllib.request
from urllib.parse import unquote
from uuid import uuid4
import wave
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def request(base, path, body=None):
    req = urllib.request.Request(base+path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.load(response)


def fixtures():
    from PIL import Image, ImageDraw, ImageFont
    archive = io.BytesIO()
    with zipfile.ZipFile(archive,'w') as document:
        document.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Documento MEDIA-42.</w:t></w:r></w:p></w:body></w:document>')
    audio = io.BytesIO()
    with wave.open(audio,'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(8000); stream.writeframes(b'\0\0'*2000)
    image = Image.new('RGB',(420,95),'white')
    ImageDraw.Draw(image).text((15,15),'42 731',font=ImageFont.truetype('/usr/share/fonts/truetype/lato/Lato-Regular.ttf',32),fill='black')
    png=io.BytesIO(); image.save(png,format='PNG')
    def box(kind,content):
        return struct.pack('>I4s',len(content)+8,kind)+content
    video=box(b'ftyp',b'isom'+bytes(4)+b'isom')+box(b'moov',box(b'mvhd',bytes(12)+struct.pack('>II',1000,2500)+bytes(80)))
    # A complete minimal PDF, extracted by the installed pdftotext utility.
    content=b'BT /F1 16 Tf 72 700 Td (PDF-MEDIA-42) Tj ET'
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
             b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
             b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
             b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream']
    pdf=b'%PDF-1.4\n'; offsets=[0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(pdf)); pdf+=f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n'
    xref=len(pdf)
    pdf+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    pdf+=f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
    return {'nota.docx':archive.getvalue(),'pagina.pdf':pdf,'imagem.png':png.getvalue(),'audio.wav':audio.getvalue(),
            'video.mp4':video,'gastos.csv':b'item,valor\nA,10.25\nB,20.75\n',
            'anterior.txt':b'valor=10','atual.txt':b'valor=20'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:3000')
    parser.add_argument('--output',type=Path,default=Path('/tmp/ia-media-smoke.json'))
    args=parser.parse_args()
    report={'schema':'local-media-smoke/v1','results':[]}
    assets={}
    try:
        for name,data in fixtures().items():
            uploaded=request(args.base_url,'/api/v1/attachments/ingest',{'schema':'local-attachment-upload/v1',
                'name':name,'data_base64':base64.b64encode(data).decode()})
            assets[name]=uploaded['attachment']['files'][0]
        cases=[('docx','Leia o conteúdo do anexo.', ['nota.docx'],'MEDIA-42','completed'),
               ('pdf','Leia o conteúdo do PDF.', ['pagina.pdf'],'PDF-MEDIA-42','completed'),
               ('ocr','Leia o texto da imagem.', ['imagem.png'],'42 731','completed'),
               ('csv','Qual a soma dos valores?', ['gastos.csv'],'31.00','completed'),
               ('audio','Qual a duração do áudio?', ['audio.wav'],'0.25','completed'),
               ('video','Qual a duração do vídeo?', ['video.mp4'],'2.5','completed'),
               ('speech-limit','Transcreva este áudio.', ['audio.wav'],'modelo próprio','blocked'),
               ('comparison','Compare os documentos.', ['anterior.txt','atual.txt'],'+valor=20','completed'),
               ('combined','Leia os dois anexos recebidos.', ['nota.docx','pagina.pdf'],'PDF-MEDIA-42','completed')]
        for name,prompt,names,marker,status in cases:
            operation='agent-core-media-'+uuid4().hex
            # Intentionally alter client text: the server must resolve the immutable asset receipt.
            attachments=[{**assets[item],'content':'FORGED-CONTENT'} for item in names]
            result=request(args.base_url,'/api/v1/agent/pursue',{'schema':'agent-request/v2','prompt':prompt,
                'objective':'auto','conversation_id':'media-'+uuid4().hex,'operation_id':operation,'attachments':attachments})
            delivery=result.get('report') or {}
            text=delivery.get('finalText') or ''
            passed=delivery.get('status')==status and marker in (text+' '+str(delivery.get('error') or '')) and 'FORGED-CONTENT' not in text
            if name=='combined': passed=passed and 'Documento MEDIA-42' in text and all(item in text for item in names)
            if name=='csv': passed=passed and '2 valores numéricos' in text and '[estrutura' not in text
            if name in {'audio','video'}: passed=passed and 'segundos' in text and '"metadata"' not in text
            if name=='ocr':
                activity=request(args.base_url,'/api/activity?after=0')
                messages=[row.get('message','') for row in activity.get('events',[]) if row.get('operation')==operation]
                reset=max((i for i,message in enumerate(messages) if message.startswith('__ANSWER_RESET__')),default=-1)
                deltas=[unquote(message.split(' · ',1)[1]) for message in messages[reset+1:] if message.startswith('__ANSWER_DELTA__ · ')]
                passed=passed and bool(deltas) and ''.join(deltas)==text
            row={'case':name,'passed':bool(passed),'status':delivery.get('status'),'final_text':text,'error':delivery.get('error')}
            report['results'].append(row)
            print(('PASS' if passed else 'FAIL')+' '+name,flush=True)
    except Exception as error:
        report['results'].append({'case':'integration','passed':False,'error':str(error)})
        print('FAIL '+str(error),flush=True)
    report['passed']=bool(report['results']) and all(row['passed'] for row in report['results'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
