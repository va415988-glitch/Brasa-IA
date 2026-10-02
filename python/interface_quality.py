"""Explicit counter-UI smoke benchmark; static checks do not prove visual quality."""
from __future__ import annotations
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def balanced_css(css):
    """Reject broken delimiters; this is deliberately not a full CSS parser."""
    stack=[];quote=None;index=0
    while index<len(css):
        char=css[index]
        if quote:
            if char=='\\':index+=2;continue
            if char==quote:quote=None
        elif css[index:index+2]=='/*':
            end=css.find('*/',index+2)
            if end<0:return False
            index=end+2;continue
        elif char in {'"',"'"}:quote=char
        elif char in '{([':stack.append(char)
        elif char in '})]':
            if not stack or stack.pop()!={'}':'{',')':'(',']':'['}[char]:return False
        index+=1
    return not stack and quote is None


class InterfaceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements, self.stack, self.scripts, self.styles = [], [], [], []
        self.language, self.viewport = '', False
    def handle_starttag(self, tag, attrs):
        row={'tag':tag, 'attrs':dict(attrs), 'text':''}
        self.elements.append(row)
        if tag=='html': self.language=row['attrs'].get('lang','')
        if tag=='meta' and row['attrs'].get('name')=='viewport': self.viewport=True
        if tag not in {'meta','link','input','br','hr','img','source','area','base','embed','wbr'}: self.stack.append(row)
    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1,-1,-1):
            if self.stack[index]['tag']==tag:
                row=self.stack[index]
                if tag=='script': self.scripts.append(row)
                if tag=='style': self.styles.append(row['text'])
                del self.stack[index:]
                break
    def handle_data(self, data):
        for row in self.stack: row['text']+=data


def evaluate_interface(plan, request=''):
    files={op['arguments']['path']:op['arguments']['content'] for op in plan.get('operations',[])
           if op.get('tool')=='create_file'}
    html=next((content for path,content in files.items() if path.endswith('.html')), '')
    parser=InterfaceParser()
    parser.feed(html)
    css='\n'.join(parser.styles+[text for name,text in files.items() if name.endswith('.css')])
    scripts=[]
    missing=[]
    for row in parser.scripts:
        source=row['attrs'].get('src')
        if source:
            path=PurePosixPath(source)
            if path.is_absolute() or '..' in path.parts or source not in files: missing.append(source)
            else: scripts.append(files[source])
        else: scripts.append(row['text'])
    checks={'html': bool(html), 'language': bool(parser.language), 'viewport':parser.viewport,
        'semantic_buttons':sum(row['tag']=='button' for row in parser.elements)>=3,
        'announced_output':any(row['attrs'].get('aria-live') or row['attrs'].get('role')=='status' for row in parser.elements),
        'focus_style':':focus-visible' in css or ':focus' in css,
        'responsive_css':'@media' in css or 'clamp(' in css,
        'local_scripts':not missing and bool(scripts), 'css_delimiters':bool(css) and balanced_css(css)}
    # Working buttons do not satisfy a brief if the model changes the product
    # name or silently substitutes another visual direction.
    named=re.search(r'\bchamada\s+(.+?):',request,re.I)
    titles=[row['text'].strip() for row in parser.elements if row['tag'] in {'title','h1'}]
    checks['requested_title']=not named or (len(titles)>=2 and all(title==named.group(1).strip() for title in titles))
    direction=True
    compact_css=css.replace(' ','').lower()
    if 'composição editorial' in request:
        direction='georgia,serif' in compact_css and '#f6f1e7' in compact_css and '#191a19' in compact_css
    elif 'composição de terminal' in request:
        direction='monospace' in compact_css and '#101916' in compact_css and '#abecb0' in compact_css
    elif 'painel dividido' in request:
        direction='display:grid' in compact_css and '#152956' in compact_css and '#ffb076' in compact_css
    elif 'aparência de caderno' in request:
        direction='repeating-linear-gradient' in compact_css and '#ffe891' in compact_css
    checks['requested_direction']=direction
    result={'checks':checks,'scope':'counter-interactions-in-simulated-dom', 'rendered_in_browser':False,
        'visual_quality_confirmed':False,'css_fingerprint':{
            'grid':'display:grid' in css.replace(' ',''), 'serif':'Georgia' in css or 'serif' in css and 'sans-serif' not in css,
            'monospace':'monospace' in css,'lined_paper':'repeating-linear-gradient' in css,
            'colors':sorted(set(re.findall(r'#[0-9a-fA-F]{3,8}\b',css)))[:12]}}
    if all(checks.values()):
        try:
            completed=subprocess.run(['node',str(ROOT/'agent-core/tools/interface-probe.mjs')],
                input=json.dumps({'elements':parser.elements,'scripts':scripts}),text=True,capture_output=True,timeout=5)
            result['behavior']=json.loads(completed.stdout) if completed.stdout else {'passed':False,'error':completed.stderr[-1000:]}
        except (OSError,subprocess.TimeoutExpired,ValueError) as error:
            result['behavior']={'passed':False,'error':str(error)[:500]}
    else: result['behavior']={'passed':False,'executed':False,'missing_script_paths':missing}
    result['passed']=all(checks.values()) and result['behavior'].get('passed') is True
    return result
