"""Offline corruption tests for the shared DMA runner; never invokes XSCT/UART."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import shutil
from types import SimpleNamespace
import zlib
sys.dont_write_bytecode=True
PROJECT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PROJECT/'scripts'))
import run_board_dma as r

def fixture(bundle,mode):
    n=512 if mode==0 else 85920;count=13 if mode==0 else 6942;calls=33 if mode==2 else 1
    stats=dict.fromkeys(r.STAT_NAMES,0)
    stats.update(samples=n,frames=count//13,tx_bytes=n*2,rx_bytes=count*24,core_status=2,input_received=n,input_consumed=n,
        output_captured=count,output_sent=count,input_bytes=n*2,output_bytes=count*24,tx_dma_status=2,rx_dma_status=2,
        rx_actual_bytes=count*24,irq_tx_count=1,irq_rx_count=1,irq_core_count=1,irq_tx_status=0x1000,irq_rx_status=0x1000,
        irq_core_status=1,events=7,wfi_count=3,failed_offset=0xffffffff,elapsed_ticks=1000000,hardware_busy_cycles=200000,wfi_ticks=800000)
    status=dict(magic=r.MAGIC,version=2,state=3,result=0,mode=mode,sequence=mode+1,samples=n,frames=count//13,records=count,
        completed=calls,warmups=3 if mode==2 else 0,repeats=30 if mode==2 else 1,pcm_crc32=zlib.crc32(bundle['pcm'][:n*2]),
        numeric_checked=count*calls,numeric_mismatches=0,core_id=bundle['metadata']['core_id'],transport=stats)
    trials=[dict(transport=copy.deepcopy(stats),result=0,mismatches=0,discarded=int(mode==2 and i<3),sequence=i) for i in range(calls)]
    data=bundle['records'][:count*24];history=data*33 if mode==2 else b''
    return status,trials,data,history

def pack_status(s):
    return struct.pack('<IIIi12I',*(s[k] for k in r.HEADER_NAMES))+struct.pack('<3Q32I',*(s['transport'][k] for k in ['elapsed_ticks','hardware_busy_cycles','wfi_ticks',*r.STAT_NAMES]))

def pack_trials(trials):
    return b''.join(struct.pack('<3Q32Ii3I',*(t['transport'][k] for k in ['elapsed_ticks','hardware_busy_cycles','wfi_ticks',*r.STAT_NAMES]),t['result'],t['mismatches'],t['discarded'],t['sequence']) for t in trials)

def run(out):
    checks=0
    def check(value,message):
        nonlocal checks;checks+=1
        if not value:raise AssertionError(message)
    results={}
    for variant in ('fixed','fp32'):
        bundle=r.references.load(variant)
        results[variant]=dict(reference=bundle['metadata'])
        for mode in range(3):
            f=fixture(bundle,mode)
            compare=lambda v:r.compare_job(*v,bundle,mode,mode+1,333333343,100000000,10000)
            check(compare(f)['passed'],'Valid fixture rejected')
            s,t,data,h=f
            raw=struct.pack('<IIIi12I',*(s[k] for k in r.HEADER_NAMES))
            raw+=struct.pack('<3Q32I',*(s['transport'][k] for k in ['elapsed_ticks','hardware_busy_cycles','wfi_ticks',*r.STAT_NAMES]))
            check(r.decode_status(raw)==s,'Status decoding')
            records=b''.join(struct.pack('<3Q32Ii3I',*(row['transport'][k] for k in ['elapsed_ticks','hardware_busy_cycles','wfi_ticks',*r.STAT_NAMES]),row['result'],row['mismatches'],row['discarded'],row['sequence']) for row in t)
            check(r.decode_trials(records)==t,'Trial decoding')
            for key in r.HEADER_NAMES:
                changed=copy.deepcopy(f);changed[0][key]+=1
                check(not compare(changed)['passed'],'Corrupt status accepted '+key)
            for key in r.STAT_NAMES+['elapsed_ticks','hardware_busy_cycles','wfi_ticks']:
                changed=copy.deepcopy(f)
                val=0 if key in ('elapsed_ticks','hardware_busy_cycles','wfi_ticks','wfi_count') else changed[1][0]['transport'][key]+1
                changed[1][0]['transport'][key]=val
                if key in ('tx_dma_status','rx_dma_status'):changed[1][0]['transport'][key]|=0x10
                # Last transport is audited separately; change only first trial.
                check(not compare(changed)['passed'],'Corrupt stats accepted '+key)
            for offset in (0,4,8,12,16,20,len(data)-1):
                changed=list(copy.deepcopy(f));bad=bytearray(h if mode==2 else data);bad[offset]^=1
                changed[3 if mode==2 else 2]=bytes(bad)
                check(not compare(changed)['passed'],'Corrupt raw/metadata accepted')
            changed=list(f);changed[2]=data[:-1];check(not compare(changed)['passed'],'Truncated result accepted')
            if mode==2:
                changed=list(f);changed[3]=h[:-24];check(not compare(changed)['passed'],'Truncated history accepted')
                changed=copy.deepcopy(f);changed[1][32]['sequence']=0;check(not compare(changed)['passed'],'Wrong last sequence accepted')
                changed=copy.deepcopy(f);changed[1][3]['discarded']=1;check(not compare(changed)['passed'],'Wrong discard boundary accepted')
                summary=r.summarize(dict(trials=t,timer_hz=333333343,pl_configured_hz=100000000))
                check(summary['repeats']==30 and summary['clip_ms']['p95']==summary['clip_ms']['median'],'Timing summary')
        results[variant]['full_reference_comparison']=r.references.compare(bundle['records'],bundle)
    args=SimpleNamespace(url='TCP:127.0.0.1:3121',target_filter='name == "Cortex-A9 #0"',cable_serial='TEST_CABLE',fpga_target_filter='name =~ "xc7z020*"',timeout=120)
    names=['arm_dma_pcm','arm_dma_results','arm_dma_history','arm_dma_trials','arm_dma_control','arm_dma_status','arm_dma_layout','arm_dma_ready_breakpoint','arm_dma_result_breakpoint','MMUTable']
    ident=dict(symbols={name:dict(address=0x100000+i*0x100000,bytes=128) for i,name in enumerate(names)},
        ps7_init='D:/offline/ps7_init.tcl',elf='D:/offline/test.elf',bit='D:/offline/test.bit',xsa='D:/offline/test.xsa',dma_descriptor_address=0x101010)
    ident['debugger_peripheral_maps']=r.peripheral_maps(PROJECT.parent/'build/system_dma/i10/design/mfcc_dma_fixed.xsa')
    for name,text in [('session',r.session_tcl(args,ident,out)),('resume',r.resume_tcl(args,ident,out)),('input',r.input_tcl(args,ident,out,85920)),('execute',r.execute_tcl(args,ident,out,2))]:
        (out/(name+'.tcl')).write_text(text,encoding='utf-8')
        check('Cortex-A9' in text and 'TEST_CABLE' in text,'Selection guards missing')
        if name=='session':
            check(text.count('rst -system -stop')==1 and text.index('rst -system -stop')<text.index('fpga -file')<text.index('dow {D:/offline/test.elf}'),'Startup order')
        else:
            check('rst ' not in text and 'fpga -file' not in text and 'dow {D:/offline/test.elf}' not in text,'Session reset in later stage')
    # Exercise the actual orchestration gates with file-producing host mocks.
    original_script,original_environment=r.run_script,r.check_environment
    environment=dict(pl_configured_hz=100000000,cpu_registers=dict(sctlr=0x1005,fpscr=0,ttbr0=0x100000,ttbcr=0),l2_cache_control=1,global_timer_control=1)
    try:
        for variant in ('fixed','fp32'):
            bundle=r.references.load(variant);ident.update(core_id=bundle['metadata']['core_id'],timer_hz=333333343)
            ident['actual_elf_layout']=list(r.layout(ident['core_id'],ident['timer_hz']))
            for mode,fault in [(0,'ok'),(1,'ok'),(2,'ok'),(1,'crc'),(1,'core_error'),(1,'missing_irq'),(1,'rx_length'),(2,'middle_raw'),(2,'last_meta')]:
                destination=out/f'job_{variant}_{mode}_{fault}';submitted=[]
                s,t,data,h=copy.deepcopy(fixture(bundle,mode))
                def fake_script(arguments,text,path):
                    path.write_text(text,encoding='utf-8');folder=path.parent
                    if path.stem=='prepare':
                        ready=copy.deepcopy(s);ready['state']=1
                        (folder/'ready_status.bin').write_bytes(pack_status(ready));(folder/'layout.bin').write_bytes(struct.pack('<32I',*ident['actual_elf_layout']))
                    elif path.stem=='input':
                        raw=bytearray((folder/'input_pcm.bin').read_bytes())
                        if fault=='crc':raw[0]^=1
                        (folder/'pcm_readback.bin').write_bytes(raw)
                    elif path.stem=='execute':
                        submitted.append(True)
                        if fault=='core_error':s['transport']['core_error_detail']=0x305;t[-1]['transport']['core_error_detail']=0x305
                        if fault=='missing_irq':s['transport']['irq_rx_count']=0;t[-1]['transport']['irq_rx_count']=0
                        if fault=='rx_length':s['transport']['rx_actual_bytes']-=4;t[-1]['transport']['rx_actual_bytes']-=4
                        altered=bytearray(h)
                        if fault=='middle_raw':altered[17*166608]^=1
                        if fault=='last_meta':altered[-4]^=1
                        (folder/'result_status.bin').write_bytes(pack_status(s));(folder/'trials.bin').write_bytes(pack_trials(t))
                        (folder/'results.bin').write_bytes(data)
                        if mode==2:(folder/'history.bin').write_bytes(altered)
                r.run_script=fake_script;r.check_environment=lambda *unused:copy.deepcopy(environment)
                args.firmware_timeout_ms=10000
                try:answer=r.job(args,ident,bundle,mode,mode+1,destination)
                except ValueError:check(fault!='ok','Valid job failed')
                else:check(fault=='ok' and answer['passed'],'Corrupt actual job accepted')
                if fault=='crc':check(not submitted,'Control submitted before CRC gate')
    finally:r.run_script,r.check_environment=original_script,original_environment
    # Plain Tcl interpreter only, with every hardware command replaced by mocks.
    tclsh=Path('C:/Xilinx/Vitis/2024.2/tps/win64/git-2.45.0/mingw64/bin/tclsh.exe')
    for fault in ('ok','already_stopped','ambiguous_cpu0','wrong_cpu0','wrong_cable','ambiguous_cpu1','stop_error','wrong_pc','unconfigured_fpga','ambiguous_fpga'):
        destination=out/('tcl_'+fault);destination.mkdir()
        mock='set fault '+fault+'\n'+'''set selected 0
set current_pc 0
proc connect {args} {puts MOCK_CONNECT}
proc targets {args} {
 if {[lindex $args 0] eq "-set"} {set ::selected [lindex $args 1];return}
 set serial MOCK_ONLY
 if {$::fault eq "wrong_cable"} {set serial OTHER}
 set name {Cortex-A9 #0}
 if {$::fault eq "wrong_cpu0"} {set name {Cortex-A9 #1}}
 set one [dict create name $name jtag_cable_serial $serial target_id 2]
 set two [dict create name {Cortex-A9 #1} jtag_cable_serial MOCK_ONLY target_id 3]
 set fpga [dict create name xc7z020 jtag_cable_serial MOCK_ONLY target_id 4]
 if {[llength $args]==1} {
  if {$::fault eq "ambiguous_cpu1"} {return [list $one $two $two]}
  return [list $one $two]
 }
 if {[lindex $args end] eq "FPGA_ONLY"} {
  if {$::fault eq "ambiguous_fpga"} {return [list $fpga $fpga]}
  return [list $fpga]
 }
 if {$::fault eq "ambiguous_cpu0"} {return [list $one $one]}
 return [list $one]
}
proc stop {args} {
 puts "MOCK_STOP $::selected"
 if {$::fault eq "already_stopped"} {error "Already stopped"}
 if {$::fault eq "stop_error"} {error "Transport failed"}
}
proc rst {args} {puts "MOCK_RESET $args"}
proc source {args} {puts MOCK_SOURCE}
proc ps7_init {args} {puts MOCK_PS_INIT}
proc ps7_post_config {args} {puts MOCK_PS_POST}
proc fpga {args} {
 if {[lindex $args 0] eq "-file"} {puts "MOCK_FPGA_PROGRAM $::selected";return}
 if {[lindex $args 0] eq "-state"} {
  if {$::fault eq "unconfigured_fpga"} {return "FPGA is not configured"}
  return "FPGA is configured"
 }
 return MOCK_CONFIG_STATUS
}
proc dow {args} {puts "MOCK_DOWNLOAD $::selected"}
proc mrd {args} {return 0}
proc loadhw {args} {
 if {[lindex $args 0] eq "-list"} {return "MOCK_HWH_PARENT_MAP"}
 if {$::selected != 2 || [llength $args]!=4 || [lindex $args 0] ne "-hw" || [lindex $args 2] ne "-mem-ranges"} {error "Bad loadhw target/options"}
 if {[lindex $args 1] ne "D:/offline/test.xsa" || [lindex $args 3] ne {{0x40400000 0x4040ffff} {0x43c00000 0x43c0ffff}}} {error "Bad loadhw XSA/ranges"}
 puts "MOCK_LOADHW $args"
}
proc bpadd {args} {set ::current_pc [lindex $args 1];return 1}
proc bpremove {args} {}
proc con {args} {puts "MOCK_CONTINUE $::selected"}
proc rrd {args} {
 set name [lindex $args end]
 if {$name eq "pc"} {
  set value $::current_pc
  if {$::fault eq "wrong_pc"} {incr value}
  return [dict create pc [format %08x $value]]
 }
 return [dict create $name 00000000]
}
'''
        execution=SimpleNamespace(url='OFFLINE_MOCK_ONLY',target_filter='CPU0_ONLY',cable_serial='MOCK_ONLY',fpga_target_filter='FPGA_ONLY',timeout=1,xsct=tclsh)
        try:r.run_script(execution,mock+r.session_tcl(execution,ident,destination),destination/'mock.tcl')
        except ValueError:check(fault not in ('ok','already_stopped'),'Valid Tcl session failed')
        else:
            check(fault in ('ok','already_stopped'),'Bad Tcl target accepted')
            log=(destination/'mock.log').read_text();order=['MOCK_RESET -system -stop','MOCK_STOP 3','MOCK_STOP 2','MOCK_PS_INIT','MOCK_FPGA_PROGRAM 4','MOCK_DOWNLOAD 2','MOCK_CONTINUE 2']
            positions=[log.index(v) for v in order]
            check(positions==sorted(positions) and log.count('MOCK_CONNECT')==1,'Tcl order/reset/connect')
    # Build a clearly labelled synthetic environment from previous verified
    # clock words plus artificial GIC/timer/DMA words. This is not DMA board data.
    previous=PROJECT.parent/'build/board_validation/board_20261005_01/accel_03'
    envdir=out/'SYNTHETIC_environment';envdir.mkdir()
    previous_ident=r.read(previous/'identity.json')
    previous_ident['timer_hz']=previous_ident['cpu_bsp_hz']//2
    for name in ('pll_registers.bin','arm_clock.bin','fpga_clock.bin','global_timer_control.bin','l2_cache_control.bin','mmio_descriptor.bin','cpu_registers.txt'):
        shutil.copy2(previous/'smoke'/name,envdir/name)
    registers=(envdir/'cpu_registers.txt').read_text()
    import re
    registers=re.sub(r'(?m)^cpsr\s+[0-9a-fA-F]+\s*$', 'cpsr 6000005f',registers)
    (envdir/'cpu_registers.txt').write_text(registers)
    artificial={'dma_descriptor.bin':[0x40400c16],'gic_cpu.bin':[1,0xf0,0],'gic_distributor.bin':[1],
        'gic_enable.bin':[1<<29,0xe0000000],'gic_pending.bin':[0,0],'gic_active.bin':[0,0],
        'gic_spi_priority.bin':[0xa0a0a000],'gic_timer_priority.bin':[0xa000],
        'gic_spi_targets.bin':[0x01010100],'gic_spi_configuration.bin':[0],
        'private_timer.bin':[0,0,0xff00,0],'dma_tx_control_status.bin':[1,2],
        'dma_rx_control_status.bin':[1,2],'dma_tx_length.bin':[171840],'dma_rx_length.bin':[166608],
        'core_identity.bin':[0x4d464343,0x20000],'core_status.bin':[2,85920],'core_diagnostics.bin':[0]*19}
    for name,words in artificial.items():(envdir/name).write_bytes(struct.pack('<'+'I'*len(words),*words))
    check(r.check_environment(envdir,previous_ident)['irq_enabled'],'Valid synthetic environment rejected')
    mutations=[('gic_cpu.bin',1,0xa0),('gic_cpu.bin',0,0),('gic_enable.bin',1,0x60000000),
        ('gic_spi_targets.bin',0,0x01020100),('gic_spi_configuration.bin',0,2<<26),
        ('gic_spi_priority.bin',0,0xa0a0a800),('gic_timer_priority.bin',0,0xa800),
        ('private_timer.bin',2,0xff04),('private_timer.bin',3,1),('gic_pending.bin',0,1<<29),
        ('gic_active.bin',1,1<<31),('dma_descriptor.bin',0,0x40400c17),('l2_cache_control.bin',0,0)]
    for name,index,value in mutations:
        path=envdir/name;original=path.read_bytes();changed=bytearray(original);struct.pack_into('<I',changed,index*4,value);path.write_bytes(changed)
        try:r.check_environment(envdir,previous_ident)
        except ValueError:check(True,'Rejected invalid environment')
        else:check(False,'Invalid environment accepted '+name)
        finally:path.write_bytes(original)
    for cpsr in ('600000df','60000053','6000007f'):
        (envdir/'cpu_registers.txt').write_text(registers.replace('6000005f',cpsr))
        try:r.check_environment(envdir,previous_ident)
        except ValueError:check(True,'CPU state rejected')
        else:check(False,'Invalid CPSR accepted')
    (envdir/'cpu_registers.txt').write_text(registers)
    result=dict(passed=True,checks=checks,variants=results,board_access=False,xsct_invoked=False,uart_access=False,plain_tclsh_mock_used=True,
                scope='Struct/reference/corruption/sequence generation tests; not actual ARM ISR execution')
    (out/'results.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False)
    result=run(a.output)
    for path in (Path(__file__),PROJECT/'scripts/run_board_dma.py',PROJECT/'verification/arm_dma/dma_references.py'):
        (a.output/path.name).write_bytes(path.read_bytes())
    index={str(v.relative_to(a.output)):hashlib.sha256(v.read_bytes()).hexdigest() for v in a.output.rglob('*') if v.is_file()}
    (a.output/'artifact_hashes.json').write_text(json.dumps(index,indent=2)+'\n')
    print('PASS ARM_DMA_RUNNER checks='+str(result['checks']))
