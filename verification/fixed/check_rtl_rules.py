"""Focused fixed-RTL source audit; actual Vivado checks remain authoritative."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[2]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def clean(text):
    text=re.sub(r'/\*.*?\*/','',text,flags=re.S)
    text=re.sub(r'//[^\n]*','',text)
    return re.sub(r'"(?:\\.|[^"\\])*"','""',text)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path);args=ap.parse_args()
    fixed=ROOT/'hardware/fixed';failures=[];files=[]
    p=json.loads((fixed/'fft/PROVENANCE.json').read_text());exempt={x['copy'] for x in p['files']}
    for item in p['files']:
        for path,want in [(Path(item['source']),item['source_sha256']),(fixed/'fft'/item['copy'],item['copy_sha256'])]:
            if sha(path)!=want:failures.append('provenance mismatch '+str(path))
    for path in sorted(fixed.rglob('*.sv')):
        if path.parent.name=='fft' and path.name in exempt:continue
        text=clean(path.read_text(encoding='utf-8'));ff=len(re.findall(r'\balways_ff\b',text));comb=len(re.findall(r'\balways_comb\b',text))
        hits=re.findall(r'\b(?:for|function|task|initial|while|repeat|forever|real|shortreal|casex)\b|\.\*|\$(?:sin|cos|ln)\b',text)
        if hits:failures.append(f'{path.name}: forbidden {hits}')
        if ff and (ff!=1 or comb!=1):failures.append(f'{path.name}: expected1 FF+1comb, got{ff}/{comb}')
        if re.search(r'@\s*\([^)]*negedge',text):failures.append(path.name+': asynchronous or negative-edge sequential process')
        if re.search(r'\b\w+_next\s*\[\s*0\s*:',text):failures.append(path.name+': whole-array next copy')
        files.append(dict(path=path.relative_to(ROOT).as_posix(),sha256=sha(path),always_ff=ff,always_comb=comb))
    # The full runtime source must not contain float constants, true division,
    # float calls, transcendental calls, or float/complex coercion.
    runtime=ROOT/'software/fixed_model/full_integer.py'
    runtime_sources={}
    for filename,selected in [('full_integer.py',None),('fft_bitmodel_wide.py',None),('qnum.py',{'signed_range','round_half_even','wrap_to_signed'})]:
        path=ROOT/'software/fixed_model'/filename;tree=ast.parse(path.read_text(encoding='utf-8'))
        # qnum also has offline float conversion helpers; audit only the three
        # integer helpers reached by this FFT, including wrap's signed_range.
        roots=[tree] if selected is None else [n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in selected]
        if selected is not None and {n.name for n in roots}!=selected:failures.append('missing integer helper '+filename)
        runtime_sources[filename]=dict(sha256=sha(path),functions=sorted(selected) if selected else 'whole module')
        for root in roots:
            for node in ast.walk(root):
                if isinstance(node,ast.Constant) and isinstance(node.value,(float,complex)):failures.append(filename+': runtime float literal')
                if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Div):failures.append(filename+': runtime true division')
                if isinstance(node,ast.Call):
                    name=node.func.id if isinstance(node.func,ast.Name) else node.func.attr if isinstance(node.func,ast.Attribute) else ''
                    if name in {'float','complex','log','log2','sin','cos','sqrt','exp'}:failures.append(filename+': runtime floating call '+name)
    result=dict(status='PASS' if not failures else 'FAIL',new_rtl_files=files,original_fft_hashes_checked=len(p['files']),full_integer_runtime_sha256=sha(runtime),runtime_sources=runtime_sources,failures=failures,scope='static source audit only; not synthesis or numerical proof')
    if args.out:args.out.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2));return bool(failures)
if __name__=='__main__':raise SystemExit(main())
