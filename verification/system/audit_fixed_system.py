"""Read-only audit of completed PS/PL integration evidence; output outside run."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[2]


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


class Audit:
    def __init__(self,run):self.run=run;self.checks=0;self.result={}
    def require(self,condition,message):
        self.checks+=1
        if not condition:raise ValueError(message)
    def execute(self):
        run=self.run;m=read(run/'run_manifest.json');hashes=read(run/'artifact_hashes.json')
        self.require(m['status']=='complete' and m['mode']=='all' and not m['smoke'],'requires full completed system run')
        self.require(m['frames']==616 and len(m['cases'])==24,'wrong full corpus')
        self.require(m['physical_board_accessed'] is False and m['evaluation_audio_used'] is False,'scope changed')
        for rel,want in hashes.items():
            p=(run/rel).resolve()
            self.require(p.is_relative_to(run) and sha(p)==want,'artifact hash: '+rel)
        for rel,want in m['source_sha256'].items():
            self.require(sha(run/'source'/rel)==want,'source snapshot: '+rel)
            if rel.startswith('hardware/') or rel.startswith('verification/system/'):
                self.require(sha(ROOT/rel)==want,'live implementation differs: '+rel)
        gold=Path(m['fixed_baseline']);gm=read(gold/'run_manifest.json')
        self.require(sha(gold/'run_manifest.json')==m['fixed_baseline_sha256'],'fixed baseline pin')
        for rel,want in m['source_sha256'].items():
            if rel.startswith('hardware/fixed/'):
                self.require(gm['source_sha256'][rel]==want,'fixed numeric RTL changed')
        provenance=read(run/'source/hardware/fixed/fft/PROVENANCE.json')
        for entry in provenance['files']:
            self.require(sha(entry['source'])==entry['source_sha256'],'original FFT changed')
        contract=Path(m['contract'])
        self.require(sha(contract/'contract.json')==m['contract_sha256']=='283fff8abec22b3455a0219e37d27ac9c090ed2e74be3f7256677fb0cf4d6e3e','numeric contract pin')
        for rel,want in read(contract/'artifact_hashes.json').items():self.require(sha(contract/rel)==want,'contract artifact: '+rel)
        for entry in m['board_source']['files']:
            self.require(sha(run/'board_source'/entry['local'])==entry['sha256'],'pinned board source')
        package_groups={}
        package_rows=(run/'reports/packaged_files.tsv').read_text().splitlines()
        self.require(package_rows[0]=='file_group\tfile_type\tpackaged_file\tidentical_source','packaged source index')
        for row in package_rows[1:]:
            group,file_type,copied,original=row.split('\t')
            copied=Path(copied).resolve();original=Path(original).resolve()
            self.require(copied.is_relative_to(run/'design/ip_repo') and original.is_relative_to(run/'source'),'packaged source location')
            self.require(sha(copied)==sha(original),'packaged source byte identity '+copied.name)
            package_groups.setdefault(group,[]).append(copied.name)
        synth_groups=[k for k in package_groups if k.startswith('xilinx_anylanguagesynthesis')]
        sim_groups=[k for k in package_groups if k.startswith('xilinx_anylanguagebehavioralsimulation')]
        self.require(len(synth_groups)==1 and len(sim_groups)==1,'package synthesis and simulation groups')
        for group in synth_groups+sim_groups:
            self.require({'mel_fw16.mem','twiddle_1024_w16.mem'}<=set(package_groups[group]),'packaged coefficient memory '+group)
        # Source restrictions are independent of successful synthesis.
        source_rules={}
        for name,expected_ff,expected_comb in [('mfcc_mmio.sv',1,1),('fixed_accel_top.sv',0,0)]:
            text=(run/'source/hardware/system'/name).read_text()
            stripped=re.sub(r'/\*.*?\*/|//[^\n]*','',text,flags=re.S)
            stripped=re.sub(r'"(?:\\.|[^"\\])*"','""',stripped)
            self.require(not re.search(r'\b(for|function|task|initial|while|repeat|forever|real|shortreal|casex)\b|\.\*',stripped),'forbidden RTL syntax '+name)
            ff=len(re.findall(r'\balways_ff\b',stripped));comb=len(re.findall(r'\balways_comb\b',stripped))
            self.require((ff,comb)==(expected_ff,expected_comb),'two-process structure '+name)
            self.require('negedge' not in stripped,'async user reset '+name)
            source_rules[name]=dict(always_ff=ff,always_comb=comb,sha256=sha(run/'source/hardware/system'/name))
        sim=read(run/'simulation.json');unit=read(run/'unit_simulation.json');ps=read(run/'ps_simulation.json')
        if 'simulation_source' in m:
            reuse=m['simulation_source'];prior=Path(reuse['path']);prior_m=read(prior/'run_manifest.json');prior_hashes=read(prior/'artifact_hashes.json')
            self.require(sha(prior/'run_manifest.json')==reuse['manifest_sha256'] and sha(prior/'artifact_hashes.json')==reuse['artifacts_sha256'],'reused simulation provenance')
            self.require(prior_m['cases']==m['cases'] and prior_m['status'] in ('complete','failed'),'reused simulation scope')
            for rel,want in m['source_sha256'].items():
                if (rel.startswith('hardware/') and rel.endswith(('.sv','.mem'))) or rel in ('verification/system/tb_fixed_accel.sv','verification/system/tb_mfcc_mmio.sv'):
                    self.require(prior_m['source_sha256'].get(rel)==want and sha(prior/'source'/rel)==want,'reused simulation source '+rel)
            for name in ('pcm.mem','mfcc.mem','shift.mem','ps_pcm.mem','ps_mfcc.mem','ps_shift.mem','cases.txt','simulation.json','unit_simulation.json','tb_fixed_accel_simulate.log','tb_mfcc_mmio_simulate.log'):
                self.require(sha(prior/name)==sha(run/name)==prior_hashes.get(name),'reused simulation evidence '+name)
        self.require(sim['status']=='PASS' and sim['frames']==616 and sim['mfcc_records']==8008 and sim['mismatches']==0,'full AXI bit comparison')
        for key in ('R_stall_cycles','B_stall_cycles','core_stall_cycles'):
            self.require(sim[key]>0,'unexercised backpressure '+key)
        self.require(sim['long_output_stall']==5000 and sim['reset_orphan_AW_W'] and sim['reset_FFT_inflight'],'missing protocol coverage')
        self.require(unit['status']=='PASS' and ps['status']=='PASS','unit/PS bus-model tests')
        self.require(unit['tests']==14 and unit['all_six_error_flags'] and unit['immediate_empty_done'] and unit['same_cycle_capture_done'] and unit['abort_preserves_R'] and unit['same_cycle_bad_read_priority'],'unit error/priority coverage')
        self.require(ps['frames']==2 and ps['samples']==672 and ps['mfcc_words']==26 and abs(ps['clock_period_ns']-10)<0.01,'PS clock and bit comparison scope')
        for key in ('reset_checks','error_checks','empty_checks','abort_checks','output_hold_checks','negative_pcm_samples','negative_mfcc_words'):
            self.require(ps[key]>0,'missing PS coverage '+key)
        self.require(not ps['arm_instructions_executed'] and not ps['physical_board_accessed'] and not ps['physical_ddr_timing_verified'],'PS bus-model scope overstated')
        self.require(m['validation']['board_implementation']=='PASS' and m['validation']['PS_bus_model_simulation']=='PASS','missing implementation status')
        summary=(run/'reports/platform_summary.txt').read_text()
        for field in ['PART=xc7z020clg400-1','ACCELERATOR_BASE=0x43C00000','ACCELERATOR_RANGE=0x00010000','BITSTREAM_INCLUDED=true','VALIDATE_BD_DESIGN=passed','PHYSICAL_BOARD_ACCESSED=false']:
            self.require(field in summary,'platform evidence '+field)
        timing=read(run/'reports/timing.json')
        self.require(timing['setup_slack_ns']>=0 and timing['hold_slack_ns']>=0,'negative slack')
        rpt=(run/'reports/timing_route.rpt').read_text()
        self.require('Vivado v.2024.2' in rpt and 'Routed' in rpt,'wrong timing report')
        timing_checks={k:int(v) for k,v in re.findall(r'checking (\w+) \((\d+)\)',rpt)}
        self.require(timing_checks.get('no_clock')==0 and timing_checks.get('unconstrained_internal_endpoints')==0,'unconstrained internal timing')
        self.require('All user specified timing constraints are met.' in rpt,'timing constraint summary')
        timing_lines=rpt.splitlines()
        timing_header=next(i for i,line in enumerate(timing_lines) if line.strip().startswith('WNS(ns)'))
        timing_values=timing_lines[timing_header+2].split()
        self.require(len(timing_values)==12 and all(int(timing_values[i])==0 for i in (2,6,10)) and float(timing_values[8])>=0,'setup/hold/pulse-width failures')
        drc=(run/'reports/drc_route.rpt').read_text()
        drc_rules=[dict(rule=match[0],severity=match[1].strip(),checks=int(match[2])) for match in re.findall(r'^\|\s*([A-Z][A-Z0-9-]+)\s*\|\s*([^|]+)\|[^|]+\|\s*(\d+)\s*\|',drc,re.M)]
        self.require(bool(drc_rules) and all(x['severity'] in ('Warning','Advisory','Info') for x in drc_rules),'route DRC errors or critical warnings')
        self.require(sum(x['checks'] for x in drc_rules)==int(re.search(r'Checks found:\s*(\d+)',drc).group(1)),'DRC summary accounting')
        bram_lines=(run/'reports/bram_cells.txt').read_text().splitlines()
        bram_counts={kind:sum(line.endswith(' '+kind) for line in bram_lines) for kind in ('RAMB36E1','RAMB18E1')}
        self.require(bram_counts=={'RAMB36E1':9,'RAMB18E1':7},'BRAM mapping changed')
        for owner in ('U_FRONT/U_PRE_RAM','U_FRONT/window_mem','u_reorder/frame_ram','U_TAIL/U_POWER_RAM','U_TAIL/U_MEL_ROM','U_BACK/U_DCT/log_mem'):
            self.require(any(owner in line for line in bram_lines),'missing BRAM owner '+owner)
        bit=Path(m['outputs']['bitstream']['path']);xsa=Path(m['outputs']['xsa']['path'])
        self.require(sha(bit)==m['outputs']['bitstream']['sha256'] and sha(xsa)==m['outputs']['xsa']['sha256'],'export hashes')
        with zipfile.ZipFile(xsa) as archive:
            names=archive.namelist();bits=[n for n in names if n.endswith('.bit')]
            self.require(len(bits)==1 and hashlib.sha256(archive.read(bits[0])).hexdigest()==sha(bit),'XSA/bit identity')
            self.require(any(n.endswith('ps7_init.tcl') for n in names),'new PS initialization missing')
        self.result=dict(run_manifest_sha256=sha(run/'run_manifest.json'),artifact_manifest_sha256=sha(run/'artifact_hashes.json'),
                         artifacts=len(hashes),source_rules=source_rules,package_groups=package_groups,simulation=sim,unit_simulation=unit,PS_bus_model=ps,
                         timing=timing,timing_checks=timing_checks,pulse_width_slack_ns=float(timing_values[8]),drc_rules=drc_rules,bram_counts=bram_counts,bitstream_sha256=sha(bit),XSA_sha256=sha(xsa))
        return self.result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',required=True,type=Path);parser.add_argument('--out',required=True,type=Path);args=parser.parse_args()
    run=args.run.resolve();out=args.out.resolve()
    if out.exists() or out.is_relative_to(run):parser.error('new audit path outside immutable run required')
    audit=Audit(run)
    report=dict(status='FAIL',run=str(run),auditor_sha256=sha(__file__),audited_at_utc=datetime.now(timezone.utc).isoformat(),board_execution=False,numerical_accuracy='NOT_ACCEPTED')
    try:report['evidence']=audit.execute();report['status']='PASS'
    except (OSError,ValueError,KeyError,TypeError) as error:report['error']=str(error)
    report['checks']=audit.checks
    with out.open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2);stream.write('\n')
    print(json.dumps({k:v for k,v in report.items() if k!='evidence'},indent=2))
    return report['status']!='PASS'


if __name__=='__main__':sys.exit(main())
