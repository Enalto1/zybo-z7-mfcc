"""Offline HWH-bound loadhw guards; all malformed XSA fixtures are new."""
import argparse
import copy
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile
sys.dont_write_bytecode=True
PROJECT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PROJECT/'scripts'))
import run_board_dma as r

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xsa',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    a=parser.parse_args();a.output.mkdir(parents=True,exist_ok=False);checks=[]
    maps=r.peripheral_maps(a.xsa)
    text=r.memory_map_tcl(dict(debugger_peripheral_maps=maps,xsa=str(a.xsa.resolve())),a.output/'map.txt')
    expected=f'loadhw -hw {r.jtag.word(a.xsa.resolve())} -mem-ranges [list {{0x40400000 0x4040ffff}} {{0x43c00000 0x43c0ffff}}]'
    assert text.splitlines()[0]==expected and '-force' not in text and 'memmap -addr' not in text
    checks.append('actual exact-XSA parent hardware map with two ranges accepted')
    with zipfile.ZipFile(a.xsa) as archive:original=archive.read(maps['hwh_member'])
    for label in ('missing_dma','duplicate_dma','wide_dma','wrong_base','wrong_master','wrong_interface','wrong_type','multiple_hwh'):
        tree=ET.fromstring(original);row=next(x for x in tree.iter('MEMRANGE') if x.get('INSTANCE')=='axi_dma_0')
        parent=next(x for x in tree.iter() if row in list(x))
        if label=='missing_dma':parent.remove(row)
        if label=='duplicate_dma':parent.append(copy.deepcopy(row))
        if label=='wide_dma':row.set('HIGHVALUE','0x4041ffff')
        if label=='wrong_base':row.set('BASEVALUE','0x40400004')
        if label=='wrong_master':row.set('MASTERBUSINTERFACE','M_AXI_GP1')
        if label=='wrong_interface':row.set('SLAVEBUSINTERFACE','M_AXI_MM2S')
        if label=='wrong_type':row.set('MEMTYPE','MEMORY')
        path=a.output/(label+'.xsa')
        with zipfile.ZipFile(path,'w') as archive:
            archive.writestr('fixture.hwh',ET.tostring(tree))
            if label=='multiple_hwh':archive.writestr('second.hwh',original)
        try:r.peripheral_maps(path)
        except ValueError as e:checks.append(dict(test=label,rejected=True,reason=str(e)))
        else:raise AssertionError('Malformed HWH accepted: '+label)
    for label in ('writable','executable','widened','shifted','third_range','missing_range','duplicate_range'):
        changed=copy.deepcopy(maps)
        if label=='writable':changed['regions'][0]['flags']=3
        if label=='executable':changed['regions'][1]['flags']=5
        if label=='widened':changed['regions'][0]['size']=0x20000
        if label=='shifted':changed['regions'][0]['address']+=4
        if label=='third_range':changed['regions'].append(dict(instance='extra',address=0x50000000,size=0x10000,flags=1))
        if label=='missing_range':changed['regions'].pop()
        if label=='duplicate_range':changed['regions'][1]=changed['regions'][0]
        try:r.memory_map_tcl(dict(debugger_peripheral_maps=changed,xsa=str(a.xsa)),a.output/'unused.txt')
        except ValueError as e:checks.append(dict(test=label,rejected=True,reason=str(e)))
        else:raise AssertionError('Unsafe map accepted: '+label)
    installed=Path('C:/Xilinx/Vitis/2024.2/scripts/xsct/xsdb/xsdb.tcl')
    source=installed.read_text();start=source.index('    setcmdmeta loadhw description {');end=source.index('\n    proc ',start)
    excerpt=source[start:end];assert '-mem-ranges [list {start1 end1} {start2 end2}]' in excerpt
    assert 'a processor, the memory map is set for all the child processors of its' in excerpt
    (a.output/'installed_loadhw_help.txt').write_text(excerpt,encoding='utf-8')
    (a.output/'scoped_loadhw.tcl').write_text(text,encoding='utf-8')
    for path in (Path(__file__),PROJECT/'scripts/run_board_dma.py'):shutil.copy2(path,a.output/path.name)
    result=dict(passed=True,check_count=len(checks),checks=checks,xsa=str(a.xsa.resolve()),xsa_sha256=r.sha(a.xsa),
        maps=maps,runner_sha256=r.sha(PROJECT/'scripts/run_board_dma.py'),
        syntax_source=dict(path=str(installed),sha256=r.sha(installed),help_excerpt_sha256=r.sha(a.output/'installed_loadhw_help.txt')),
        board_accessed=False,xsct_invoked=False,shared_jtag_changed=False)
    r.dump(a.output/'results.json',result)
    r.dump(a.output/'artifact_hashes.json',{p.relative_to(a.output).as_posix():r.sha(p) for p in a.output.rglob('*') if p.is_file()})
    print('PASS MEMMAP_GUARDS checks='+str(len(checks)))
if __name__=='__main__':main()
