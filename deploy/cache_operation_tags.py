"""Restore missing usage labels without changing provider request arguments."""
import ast


def tag_chat_calls(source,operation,*,function=None):
    tree=ast.parse(source)
    scopes=[node for node in ast.walk(tree) if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))
            and node.name==function] if function else [tree]
    raw=source.encode('utf-8');lines=raw.splitlines(keepends=True);edits=[]
    for scope in scopes:
        for node in ast.walk(scope):
            if (not isinstance(node,ast.Call) or not isinstance(node.func,ast.Attribute) or node.func.attr!='chat'
                    or any(k.arg=='operation' for k in node.keywords)):continue
            end=sum(map(len,lines[:node.end_lineno-1]))+node.end_col_offset
            if raw[end-1:end]!=b')':raise ValueError('Unexpected call boundary')
            before=raw[:end-1].rstrip()
            insertion=('' if before.endswith(b',') else ',')+' operation='+repr(operation)
            edits.append((end-1,insertion.encode()))
    for at,value in sorted(edits,reverse=True):raw=raw[:at]+value+raw[at:]
    result=raw.decode();compile(result,'operation-tagged','exec')
    return result,len(edits)
