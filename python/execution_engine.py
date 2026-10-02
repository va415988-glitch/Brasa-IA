"""Bounded interpreter for expressions and pure Python functions.

This interprets an explicit AST subset. It never imports or executes a module,
and does not expose Python's eval/exec, filesystem, network or host objects.
Unsupported Python is reported instead of being silently approximated.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import math
import operator
from pathlib import Path
import time
import types

MAX_STEPS=20000
MAX_ITEMS=1000
MAX_TEXT=8192
MAX_SOURCE=131072
MAX_RESULT=32768
MAX_INTEGER_BITS=4096


class EngineError(ValueError): pass
class UnsupportedSyntax(EngineError): pass
class LimitExceeded(EngineError): pass


class Flow(Exception):
    def __init__(self,kind,value=None):self.kind,self.value=kind,value


class BoundFunction:
    def __init__(self,engine,node,scope):self.engine,self.node,self.scope=engine,node,scope
    def __call__(self,*args,**kwargs):return self.engine.invoke(self.node,self.scope,args,kwargs)


def check_value(value,depth=0,budget=None):
    # Count repeated references too: JSON serialization expands aliases.
    if budget is None:budget=[10000,MAX_RESULT]
    budget[0]-=1;budget[1]-=1
    if isinstance(value,str):budget[1]-=len(value)
    if budget[0]<0 or budget[1]<0:raise LimitExceeded('Valor agregado excede 10000 nós ou 32 KiB de conteúdo.')
    if depth>16:raise LimitExceeded('Valor excede a profundidade máxima de 16.')
    if value is None or isinstance(value,bool):return value
    if isinstance(value,int):
        if value.bit_length()>MAX_INTEGER_BITS:raise LimitExceeded('Inteiro excede 4096 bits.')
    elif isinstance(value,float):
        if not math.isfinite(value):raise EngineError('Resultado numérico não finito.')
    elif isinstance(value,str):
        if len(value)>MAX_TEXT:raise LimitExceeded('Texto excede 8192 caracteres.')
    elif isinstance(value,(list,tuple,set,dict)):
        if len(value)>MAX_ITEMS:raise LimitExceeded('Coleção excede 1000 itens.')
        for item in value:
            check_value(item,depth+1,budget)
            if isinstance(value,dict):check_value(value[item],depth+1,budget)
    elif isinstance(value,range):
        if len(value)>MAX_ITEMS:raise LimitExceeded('range excede 1000 itens.')
    elif isinstance(value,(BoundFunction,types.GeneratorType,enumerate,zip,reversed)):
        pass  # Internal values only; they cannot be serialized as a result.
    else:raise UnsupportedSyntax('Tipo não suportado: '+type(value).__name__)
    return value


def json_value(value):
    if isinstance(value,int) and not isinstance(value,bool) and abs(value)>2**53-1:
        return {'integer_decimal':str(value)}
    if isinstance(value,(list,tuple)):return [json_value(item) for item in value]
    if isinstance(value,dict):
        if any(not isinstance(key,str) for key in value):
            raise UnsupportedSyntax('O resultado JSON exige chaves de texto em dicionários.')
        return {key:json_value(item) for key,item in value.items()}
    if value is None or isinstance(value,(str,int,float,bool)):return value
    raise UnsupportedSyntax('Resultado não representável em JSON: '+type(value).__name__)


class Interpreter:
    binary={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,
            ast.FloorDiv:operator.floordiv,ast.Mod:operator.mod,ast.Pow:operator.pow}
    comparisons={ast.Eq:operator.eq,ast.NotEq:operator.ne,ast.Lt:operator.lt,ast.LtE:operator.le,
                 ast.Gt:operator.gt,ast.GtE:operator.ge,ast.In:lambda a,b:a in b,ast.NotIn:lambda a,b:a not in b}
    builtins={'abs':abs,'round':round,'len':len,'int':int,'float':float,'str':str,'bool':bool}
    methods={str:{'lower','upper','strip','lstrip','rstrip','split','join','replace','startswith','endswith','find','count'},
             list:{'append','extend','pop','sort','reverse','count','index','copy'},
             dict:{'get','pop','copy','update','keys','values','items'}}

    def __init__(self,functions=None):
        self.functions=functions or {};self.steps=0;self.depth=0;self.calls=0;self.line=None
        self.deadline=time.monotonic()+1.0;self.trace=[];self.defaults={}
    def tick(self,node=None):
        self.steps+=1
        if node is not None:self.line=getattr(node,'lineno',self.line)
        if self.steps>MAX_STEPS or time.monotonic()>self.deadline:
            raise LimitExceeded('Orçamento de 20000 operações ou 1 segundo esgotado.')
    def items(self,value):
        for index,item in enumerate(value):
            self.tick()
            if index>=MAX_ITEMS:raise LimitExceeded('Iteração excede 1000 itens.')
            yield check_value(item)
    def binary_value(self,operation,left,right,inplace=False):
        kind=type(operation)
        if kind not in self.binary:raise UnsupportedSyntax('Operador não suportado: '+kind.__name__)
        if kind==ast.Mod and isinstance(left,str):
            raise UnsupportedSyntax('Interpolação de texto com % não é suportada.')
        if kind==ast.Pow:
            if not isinstance(right,(int,float)) or abs(right)>256:
                raise LimitExceeded('Expoente fora do limite de 256.')
            if isinstance(left,int) and isinstance(right,int) and right>0 and left.bit_length()*right>MAX_INTEGER_BITS:
                raise LimitExceeded('Potência excederia o orçamento do inteiro.')
        if kind==ast.Mult:
            for sequence,count in ((left,right),(right,left)):
                if isinstance(sequence,(str,list,tuple)) and isinstance(count,int):
                    limit=MAX_TEXT if isinstance(sequence,str) else MAX_ITEMS
                    if len(sequence)*max(0,count)>limit:raise LimitExceeded('Repetição excederia o limite da coleção.')
        if kind==ast.Add and isinstance(left,(str,list,tuple)) and isinstance(right,type(left)):
            limit=MAX_TEXT if isinstance(left,str) else MAX_ITEMS
            if len(left)+len(right)>limit:raise LimitExceeded('Concatenação excederia o limite da coleção.')
        if inplace and isinstance(left,list) and kind in (ast.Add,ast.Mult):
            return check_value((operator.iadd if kind==ast.Add else operator.imul)(left,right))
        return check_value(self.binary[kind](left,right))
    def expression(self,node,scope):
        self.tick(node);self.depth+=1
        try:
            if self.depth>64:raise LimitExceeded('Expressão excede 64 níveis.')
            return check_value(self._expression(node,scope))
        finally:self.depth-=1
    def _expression(self,node,scope):
        if isinstance(node,ast.Constant):return node.value
        if isinstance(node,ast.Name):
            if node.id in scope:return scope[node.id]
            if node.id in self.functions:return BoundFunction(self,self.functions[node.id],{})
            raise UnsupportedSyntax('Nome não disponível no motor: '+node.id)
        if isinstance(node,(ast.List,ast.Tuple,ast.Set)):
            values=[self.expression(item,scope) for item in node.elts]
            return tuple(values) if isinstance(node,ast.Tuple) else set(values) if isinstance(node,ast.Set) else values
        if isinstance(node,ast.Dict):
            if any(key is None for key in node.keys):raise UnsupportedSyntax('Expansão de dicionário não suportada.')
            return {self.expression(key,scope):self.expression(value,scope) for key,value in zip(node.keys,node.values)}
        if isinstance(node,ast.BinOp):return self.binary_value(node.op,self.expression(node.left,scope),self.expression(node.right,scope))
        if isinstance(node,ast.UnaryOp):
            operations={ast.UAdd:operator.pos,ast.USub:operator.neg,ast.Not:operator.not_}
            if type(node.op) not in operations:raise UnsupportedSyntax('Operador unário não suportado.')
            return operations[type(node.op)](self.expression(node.operand,scope))
        if isinstance(node,ast.BoolOp):
            for item in node.values:
                value=self.expression(item,scope)
                if isinstance(node.op,ast.And) and not value or isinstance(node.op,ast.Or) and value:return value
            return value
        if isinstance(node,ast.Compare):
            left=self.expression(node.left,scope)
            for operation,item in zip(node.ops,node.comparators):
                right=self.expression(item,scope)
                if isinstance(operation,(ast.Is,ast.IsNot)):
                    if left is not None and right is not None:raise UnsupportedSyntax('Identidade só é suportada para None.')
                    matched=(left is right) if isinstance(operation,ast.Is) else (left is not right)
                elif type(operation) in self.comparisons:matched=self.comparisons[type(operation)](left,right)
                else:raise UnsupportedSyntax('Comparação não suportada.')
                if not matched:return False
                left=right
            return True
        if isinstance(node,ast.IfExp):return self.expression(node.body if self.expression(node.test,scope) else node.orelse,scope)
        if isinstance(node,ast.Subscript):return self.expression(node.value,scope)[self.index(node.slice,scope)]
        if isinstance(node,ast.Lambda):return BoundFunction(self,node,scope)
        if isinstance(node,(ast.ListComp,ast.GeneratorExp,ast.SetComp)):
            inner_scope=dict(scope)
            # Python evaluates the outer iterable when creating a generator.
            first=iter(self.expression(node.generators[0].iter,scope))
            iterator=self.comprehension(node,0,inner_scope,first)
            return iterator if isinstance(node,ast.GeneratorExp) else set(self.items(iterator)) if isinstance(node,ast.SetComp) else list(self.items(iterator))
        if isinstance(node,ast.Call):
            if any(isinstance(arg,ast.Starred) for arg in node.args) or any(key.arg is None for key in node.keywords):
                raise UnsupportedSyntax('Expansão de argumentos não suportada.')
            if len({key.arg for key in node.keywords})!=len(node.keywords):raise EngineError('Argumento nomeado duplicado.')
            builtin_name=None;receiver=None;callable_=None
            if isinstance(node.func,ast.Name) and node.func.id not in scope and node.func.id not in self.functions:
                builtin_name=node.func.id
            elif isinstance(node.func,ast.Attribute):
                receiver=self.expression(node.func.value,scope)
                if node.func.attr not in self.methods.get(type(receiver),set()):
                    raise UnsupportedSyntax('Método não permitido: '+node.func.attr)
            else:
                callable_=self.expression(node.func,scope)
                if not isinstance(callable_,BoundFunction):raise UnsupportedSyntax('Chamada não pertence ao motor.')
            args=[self.expression(item,scope) for item in node.args]
            kwargs={item.arg:self.expression(item.value,scope) for item in node.keywords}
            if builtin_name is not None:return self.builtin(builtin_name,args,kwargs)
            if isinstance(node.func,ast.Attribute):
                value=receiver;name=node.func.attr
                if name=='extend' and args:args[0]=list(self.items(args[0]))
                if name=='join' and args:
                    args[0]=list(self.items(args[0]))
                    if all(isinstance(item,str) for item in args[0]) and sum(map(len,args[0]))+max(0,len(args[0])-1)*len(value)>MAX_TEXT:
                        raise LimitExceeded('join excederia 8192 caracteres.')
                if name=='replace' and isinstance(value,str) and len(args)>=2 and all(isinstance(item,str) for item in args[:2]):
                    count=value.count(args[0]);requested=args[2] if len(args)>2 else kwargs.get('count',-1)
                    if isinstance(requested,int) and requested>=0:count=min(count,requested)
                    if len(value)+count*(len(args[1])-len(args[0]))>MAX_TEXT:raise LimitExceeded('replace excederia 8192 caracteres.')
                # All receivers are interpreter-owned primitives, never host objects.
                result=getattr(value,name)(*args,**kwargs)
                if isinstance(result,(type({}.keys()),type({}.values()),type({}.items()))):result=list(self.items(result))
                check_value(value)
                return result
            return callable_(*args,**kwargs)
        raise UnsupportedSyntax('Expressão não suportada: '+type(node).__name__)
    def index(self,node,scope):
        if isinstance(node,ast.Slice):return slice(*(self.expression(item,scope) if item else None for item in (node.lower,node.upper,node.step)))
        return self.expression(node,scope)
    def comprehension(self,node,offset,scope,first=None):
        if offset==len(node.generators):
            yield self.expression(node.elt,scope);return
        generator=node.generators[offset]
        if generator.is_async:raise UnsupportedSyntax('Compreensão assíncrona não suportada.')
        iterable=first if offset==0 and first is not None else self.expression(generator.iter,scope)
        for item in self.items(iterable):
            self.assign(generator.target,item,scope)
            if all(self.expression(test,scope) for test in generator.ifs):
                yield from self.comprehension(node,offset+1,scope)
    def builtin(self,name,args,kwargs):
        if name in self.builtins:return self.builtins[name](*args,**kwargs)
        if name=='range':return range(*args,**kwargs)
        if name in {'list','tuple','set'}:
            if kwargs or len(args)>1:raise UnsupportedSyntax('Argumentos inválidos para '+name)
            values=list(self.items(args[0])) if args else []
            return {'list':list,'tuple':tuple,'set':set}[name](values)
        if name in {'sum','all','any','sorted','reversed','enumerate'}:
            if not args:raise UnsupportedSyntax('Falta a coleção para '+name)
            iterator=self.items(args[0])
            functions={'sum':sum,'all':all,'any':any,'sorted':sorted,'reversed':lambda items:reversed(list(items)),'enumerate':enumerate}
            return functions[name](iterator,*args[1:],**kwargs)
        if name in {'min','max'}:
            values=(self.items(args[0]),) if len(args)==1 else tuple(args)
            return (min if name=='min' else max)(*values,**kwargs)
        if name=='zip':return zip(*(self.items(arg) for arg in args),**kwargs)
        raise UnsupportedSyntax('Função não permitida: '+name)
    def assign(self,node,value,scope):
        self.tick(node)
        if isinstance(node,ast.Name):scope[node.id]=check_value(value)
        elif isinstance(node,(ast.Tuple,ast.List)):
            values=list(self.items(value))
            if len(values)!=len(node.elts):raise EngineError('Desestruturação com quantidade incompatível.')
            for target,item in zip(node.elts,values):self.assign(target,item,scope)
        elif isinstance(node,ast.Subscript):
            receiver=self.expression(node.value,scope);index=self.index(node.slice,scope)
            if not isinstance(receiver,(list,dict)):raise UnsupportedSyntax('Atribuição exige lista ou dicionário.')
            receiver[index]=value;check_value(receiver)
        else:raise UnsupportedSyntax('Atribuição não suportada: '+type(node).__name__)
    def statements(self,nodes,scope):
        for node in nodes:
            self.tick(node)
            if isinstance(node,ast.Return):raise Flow('return',self.expression(node.value,scope) if node.value else None)
            elif isinstance(node,ast.Assign):
                value=self.expression(node.value,scope)
                for target in node.targets:self.assign(target,value,scope)
            elif isinstance(node,ast.AnnAssign):
                if node.value:self.assign(node.target,self.expression(node.value,scope),scope)
            elif isinstance(node,ast.AugAssign):
                if not isinstance(node.target,ast.Name):raise UnsupportedSyntax('Atribuição aumentada só aceita variável simples.')
                self.assign(node.target,self.binary_value(node.op,self.expression(node.target,scope),self.expression(node.value,scope),inplace=True),scope)
            elif isinstance(node,ast.Expr):self.expression(node.value,scope)
            elif isinstance(node,ast.If):self.statements(node.body if self.expression(node.test,scope) else node.orelse,scope)
            elif isinstance(node,(ast.For,ast.While)):
                broken=False
                iterator=self.items(self.expression(node.iter,scope)) if isinstance(node,ast.For) else None
                while True:
                    self.tick(node)
                    if iterator is None:
                        if not self.expression(node.test,scope):break
                    else:
                        try:item=next(iterator)
                        except StopIteration:break
                        self.assign(node.target,item,scope)
                    try:self.statements(node.body,scope)
                    except Flow as flow:
                        if flow.kind=='break':broken=True;break
                        if flow.kind!='continue':raise
                if not broken:self.statements(node.orelse,scope)
            elif isinstance(node,ast.Break):raise Flow('break')
            elif isinstance(node,ast.Continue):raise Flow('continue')
            elif isinstance(node,ast.Pass):pass
            else:raise UnsupportedSyntax('Instrução não suportada: '+type(node).__name__)
    def invoke(self,node,closure,args,kwargs):
        self.calls+=1
        try:
            if self.calls>24:raise LimitExceeded('Chamadas excedem a profundidade de 24.')
            self.tick(node)
            signature=node.args
            if signature.vararg or signature.kwarg or signature.kwonlyargs:
                raise UnsupportedSyntax('Assinatura variável ou exclusivamente nomeada não suportada.')
            if getattr(node,'decorator_list',[]):raise UnsupportedSyntax('Decoradores não são executados pelo motor.')
            parameters=signature.posonlyargs+signature.args;names=[parameter.arg for parameter in parameters]
            if len(args)>len(names):raise EngineError('Argumentos posicionais em excesso.')
            scope=dict(closure);assigned=set()
            for name,value in zip(names,args):scope[name]=value;assigned.add(name)
            for name,value in kwargs.items():
                if name not in names or name in assigned or name in {p.arg for p in signature.posonlyargs}:
                    raise EngineError('Argumento nomeado inválido ou duplicado: '+name)
                scope[name]=value;assigned.add(name)
            if id(node) not in self.defaults:
                self.defaults[id(node)]=[check_value(ast.literal_eval(value)) for value in signature.defaults]
            defaults=dict(zip(names[len(names)-len(signature.defaults):],self.defaults[id(node)]))
            for name in names:
                if name not in assigned:
                    if name not in defaults:raise EngineError('Argumento obrigatório ausente: '+name)
                    scope[name]=defaults[name]
            if len(self.trace)<40:self.trace.append({'function':getattr(node,'name','lambda'),'line':node.lineno})
            if isinstance(node,ast.Lambda):return self.expression(node.body,scope)
            try:self.statements(node.body,scope)
            except Flow as flow:
                if flow.kind=='return':return check_value(flow.value)
                raise UnsupportedSyntax('Controle de loop fora de um loop.')
            return None
        finally:self.calls-=1


def function_source(workspace,path):
    root=Path(workspace).resolve(strict=True);relative=Path(path)
    if relative.is_absolute() or '..' in relative.parts or '\\' in path or relative.suffix!='.py':
        raise EngineError('A função precisa de um arquivo .py relativo ao workspace.')
    current=root
    for part in relative.parts:
        current=current/part
        if current.is_symlink():raise EngineError('Links simbólicos não são aceitos.')
    current=current.resolve(strict=True)
    if not current.is_relative_to(root) or not current.is_file():raise EngineError('Arquivo fora do workspace.')
    if current.stat().st_size>MAX_SOURCE:raise LimitExceeded('Arquivo excede 128 KiB.')
    return current.read_text(encoding='utf-8')


def run_engine(tool,arguments,workspace=None):
    started=time.monotonic();interpreter=Interpreter()
    response={'schema':'execution-engine/v1','engine':'bounded-python-ast/v1','tool':tool,
        'request':arguments,'executed':False,'passed':False,'top_level_executed':False,
        'general_python_execution':False,'result':None,'error':None,
        'limits':{'max_steps':MAX_STEPS,'max_seconds':1,'max_items':MAX_ITEMS,'max_call_depth':24}}
    try:
        if not isinstance(arguments,dict):raise EngineError('arguments precisa de objeto JSON.')
        if len(json.dumps(arguments).encode())>MAX_RESULT:raise LimitExceeded('Entrada excede 32 KiB.')
        # Mutable function arguments belong to this evaluation. Keep the
        # request evidence and the caller's objects unchanged.
        arguments=json.loads(json.dumps(arguments))
        if tool=='calculate':
            if set(arguments)-{'expression','variables'}:raise EngineError('Campos não permitidos para calculate.')
            expression=arguments.get('expression');variables=arguments.get('variables',{})
            if not isinstance(expression,str) or not expression.strip() or len(expression)>MAX_TEXT:
                raise EngineError('expression precisa de texto com até 8192 caracteres.')
            if not isinstance(variables,dict):raise EngineError('variables precisa de objeto JSON.')
            check_value(variables);node=ast.parse(expression,mode='eval')
            if sum(1 for _ in ast.walk(node))>10000:raise LimitExceeded('AST excede 10000 nós.')
            response['executed']=True;value=interpreter.expression(node.body,variables)
        elif tool=='evaluate_function':
            if set(arguments)-{'path','function','args','kwargs'}:raise EngineError('Campos não permitidos para evaluate_function.')
            name=arguments.get('function');path=arguments.get('path')
            if not isinstance(name,str) or not name.isidentifier() or not isinstance(path,str):
                raise EngineError('Informe path e function válidos.')
            args=arguments.get('args',[]);kwargs=arguments.get('kwargs',{})
            if not isinstance(args,list) or not isinstance(kwargs,dict):raise EngineError('args precisa de array e kwargs de objeto.')
            check_value(args);check_value(kwargs)
            source=function_source(workspace,path);module=ast.parse(source)
            if sum(1 for _ in ast.walk(module))>10000:raise LimitExceeded('AST excede 10000 nós.')
            interpreter.functions={item.name:item for item in module.body if isinstance(item,ast.FunctionDef)}
            if name not in interpreter.functions:raise UnsupportedSyntax('Função de nível superior não encontrada: '+name)
            response.update(path=path,function=name,source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                            source_line=interpreter.functions[name].lineno)
            response['executed']=True;value=interpreter.invoke(interpreter.functions[name],{},args,kwargs)
        else:raise EngineError('Motor não registrado: '+str(tool))
        result=json_value(check_value(value))
        if len(json.dumps(result).encode())>MAX_RESULT:raise LimitExceeded('Resultado excede 32 KiB.')
        response.update(passed=True,result=result,result_type=type(value).__name__,encoding='json-with-tagged-large-integers')
    except (EngineError,SyntaxError,TypeError,KeyError,IndexError,ZeroDivisionError,OverflowError,RecursionError,OSError,UnicodeError,ValueError) as error:
        response['error']={'type':type(error).__name__,'message':str(error)[:500],'line':getattr(error,'lineno',None) or interpreter.line}
    response.update(steps=interpreter.steps,calls=interpreter.trace,elapsed_ms=round((time.monotonic()-started)*1000,2))
    return response


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--workspace',required=True)
    parser.add_argument('--tool',required=True,choices=['calculate','evaluate_function']);parser.add_argument('--arguments',required=True)
    args=parser.parse_args()
    print(json.dumps(run_engine(args.tool,json.loads(args.arguments),args.workspace),ensure_ascii=False,allow_nan=False))


if __name__=='__main__':main()
