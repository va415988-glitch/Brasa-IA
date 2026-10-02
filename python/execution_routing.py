"""Extract explicit computation requests, keeping data separate from commands."""
from __future__ import annotations
import ast
import json
import re
import io
import tokenize


def before_question(text):
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.string=='?' and token.type in (tokenize.ERRORTOKEN, tokenize.OP):
                lines=text.splitlines(keepends=True)
                return ''.join(lines[:token.start[0]-1])+lines[token.start[0]-1][:token.start[1]]
    except (tokenize.TokenError,IndentationError):pass
    return text


def engine_request(question):
    text=str(question).strip()
    # Function call notation and JSON arguments both preserve literal inputs.
    match=re.fullmatch(r'(?:execute|rode|avalie|teste)\s+(?:a\s+fun[cç][aã]o\s+)?([A-Za-z_]\w*)\s+em\s+([\w./-]+\.py)\s+com\s+argumentos(?:\s+JSON)?\s*:?\s*(.+?)\s*[.]?',text,re.I|re.S)
    if match:
        try:arguments=json.loads(match[3])
        except (ValueError,RecursionError):return None
        if isinstance(arguments,list):args,kwargs=arguments,{}
        elif isinstance(arguments,dict) and not set(arguments)-{'args','kwargs'}:
            args,kwargs=arguments.get('args',[]),arguments.get('kwargs',{})
        else:return None
        if not isinstance(args,list) or not isinstance(kwargs,dict):return None
        return 'evaluate_function',{'path':match[2],'function':match[1],'args':args,'kwargs':kwargs}
    match=re.fullmatch(r'(?:execute|rode|avalie|teste)\s+(?:a\s+fun[cç][aã]o\s+)?(.+\))\s+em\s+([\w./-]+\.py)\s*[.]?',text,re.I|re.S)
    if match:
        try:
            call=ast.parse(match[1],mode='eval').body
            if not isinstance(call,ast.Call) or not isinstance(call.func,ast.Name) or any(key.arg is None for key in call.keywords):return None
            if len({key.arg for key in call.keywords})!=len(call.keywords):return None
            args=[ast.literal_eval(item) for item in call.args]
            kwargs={key.arg:ast.literal_eval(key.value) for key in call.keywords}
            # Only JSON literals can cross the tool boundary.
            json.dumps([args,kwargs],allow_nan=False)
            return 'evaluate_function',{'path':match[2],'function':call.func.id,'args':args,'kwargs':kwargs}
        except (SyntaxError,ValueError,TypeError,RecursionError):return None
    match=re.fullmatch(r'(?:calcule|avalie a express[aã]o|quanto (?:é|e|d[aá])|qual o resultado de)\s*:?\s*(.+)',text,re.I|re.S)
    if not match:return None
    expression=before_question(match[1]).strip()
    variables={}
    pieces=re.split(r'\s+com\s+vari[aá]veis\s*:?\s*',expression,maxsplit=1,flags=re.I)
    if len(pieces)==2:
        expression=pieces[0]
        try:variables=json.loads(pieces[1])
        except (ValueError,RecursionError):return None
        if not isinstance(variables,dict):return None
    # Normalize operator spellings without changing text inside literals.
    if not any(char in expression for char in ('"',"'")):
        expression=expression.replace('×','*').replace('÷','/');expression=re.sub(r'\bvezes\b','*',expression,flags=re.I)
    percent=re.fullmatch(r'(-?\d+(?:\.\d+)?)\s*%\s+de\s+(-?\d+(?:\.\d+)?)',expression)
    if percent:expression=f'({percent[1]} / 100) * {percent[2]}'
    try:node=ast.parse(expression,mode='eval')
    except (SyntaxError,ValueError,RecursionError):return None
    # Ordinary questions such as “quanto é caro” must not select an engine.
    if isinstance(node.body,ast.Name) and node.body.id not in variables \
            and not re.match(r'^(?:calcule|avalie a express[aã]o)\b',text,re.I):return None
    return 'calculate',{'expression':expression,'variables':variables}


def execution_summary(data):
    if data.get('schema')!='execution-engine/v1' or not data.get('passed'):
        error=data.get('error') or {}
        return 'A avaliação não foi concluída: '+str(error.get('message') or 'o motor não confirmou um resultado.')
    result=json.dumps(data['result'],ensure_ascii=False,allow_nan=False)
    if data.get('tool')=='evaluate_function':
        return (f"Resultado de `{data.get('function')}` em `{data.get('path')}:{data.get('source_line')}`: `{result}`.\n\n"
                'Avaliação pelo motor AST local, para os argumentos informados.')
    return f'Resultado: `{result}`.\n\nExpressão calculada no motor local: `{data["request"]["expression"]}`.'
