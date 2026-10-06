"""Offline-first shared fixed/FP32 AXI DMA + GIC runner. Hardware requires --execute.

One reset/program/download per variant; smoke, development and 3+30 timed clips
share the running ELF. Never starts a hardware server or writes flash.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
from pathlib import Path
import re
import shutil
import statistics
import struct
import sys
import traceback
import xml.etree.ElementTree as ET
import zipfile
import zlib

sys.dont_write_bytecode=True
PROJECT=Path(__file__).resolve().parents[1]
BUILD=PROJECT.parent/'build'
from run_board_fp32_accel import Elf,read,sha,dump,require,guarded_continue,run_script,check_clocks as base_clocks,clock_capture as base_capture
from build_arm_dma import xsa_configuration
from run_dma_system import verify_fixed_evidence, verify_mel_binding, isolation_module
sys.path.insert(0,str(PROJECT/'verification/arm'))
import jtag
from clocks import expected_configuration
from uart import Capture
sys.path.insert(0,str(PROJECT/'verification/arm_dma'))
import dma_references as references

MAGIC=0x444d4143
STAT_NAMES=('samples frames tx_bytes rx_bytes core_status core_error_flags core_error_detail input_received input_consumed '
 'output_captured output_sent input_bytes output_bytes tx_dma_status rx_dma_status rx_actual_bytes '
 'irq_tx_count irq_rx_count irq_core_count irq_timer_count irq_tx_status irq_rx_status irq_core_status events '
 'wfi_count deadline_fired recovery_attempted recovery_failed failed_offset reserved0 reserved1 reserved2').split()
HEADER_NAMES=('magic version state result mode sequence samples frames records completed warmups repeats pcm_crc32 '
              'numeric_checked numeric_mismatches core_id').split()
BOUNDARY=('Global timer starts after read-only identity probe, before per-clip ABORT/count/IRQ configuration, '
 'input cache flush, output flush/invalidate, one-shot deadline timer, S2MM-first DMA setup and START/MM2S. '
 'Stops after applicable MM2S IOC + S2MM IOC + terminal core DONE IRQ join, final core/DMA counters/status/actual '
 'receive-length checks, interrupt cleanup and required output invalidation. Numeric/metadata comparison and '
 'history copy occur after the timestamp. Excludes JTAG/UART, initial CRC, PS/FPGA/ELF setup. '
 'Three warmup and thirty measured clips share one ELF and buffers; every output is retained. '
 'WFI ticks bracket the wait instruction and its overhead, not a direct CPU utilization measurement. '
 'PL busy cycles include stream stalls, serialization and native reset; they are not isolated compute latency.')
STARTUP=('Once per variant session: same-cable CPU0 selection, system reset, CPU1 halt, same-XSA PS init, '
 'FPGA programming, post_config, ELF download. Subsequent READY/result phases only resume the same ELF; '
 'no reset/program/download between clips or timing trials. Each debugger connection loads only the two '
 'exact-XSA 64KiB GP0 peripheral windows into the ARM parent debugger map with their HWH access attributes. '
 'Runner peripheral operations are reads; no force/global memory-access override is used.')

def peripheral_maps(xsa):
    """Authenticate the only PL ranges that this runner may expose to XSCT mrd."""
    with zipfile.ZipFile(xsa) as archive:
        names=[name for name in archive.namelist() if name.endswith('.hwh')]
        require(len(names)==1,'Expected one exact-XSA HWH for debugger map')
        content=archive.read(names[0]);tree=ET.fromstring(content)
    expected={'axi_dma_0':(0x40400000,'S_AXI_LITE'),'mfcc_dma_0':(0x43c00000,'S_AXI')}
    result=[]
    for instance,(base,interface) in expected.items():
        rows=[row for row in tree.iter('MEMRANGE') if row.get('INSTANCE')==instance]
        require(len(rows)==1,'Missing/ambiguous HWH peripheral map: '+instance)
        row=rows[0]
        require(row.get('MASTERBUSINTERFACE')=='M_AXI_GP0' and row.get('SLAVEBUSINTERFACE')==interface
            and row.get('MEMTYPE')=='REGISTER','Unexpected HWH peripheral interface: '+instance)
        require(int(row.get('BASEVALUE','-1'),0)==base and int(row.get('HIGHVALUE','-1'),0)==base+0xffff,
            'Peripheral map must be the exact expected 64KiB range: '+instance)
        result.append(dict(instance=instance,address=base,size=0x10000))
    return dict(hwh_member=names[0],hwh_sha256=hashlib.sha256(content).hexdigest(),regions=result,
                method='loadhw -hw exact XSA -mem-ranges two explicit ranges',
                access_attributes='Inherited from XSA/HWH; not a read-only permission override')

def memory_map_tcl(ident,path):
    regions=ident['debugger_peripheral_maps']['regions']
    expected=[dict(instance='axi_dma_0',address=0x40400000,size=0x10000),
              dict(instance='mfcc_dma_0',address=0x43c00000,size=0x10000)]
    require(regions==expected,'Refusing widened/extra/unexpected debugger peripheral map')
    # XSCT2024.2 loadhw/set_memmap deliberately installs ARM maps on the parent
    # context. CPU-local memmap entries do not satisfy Zynq PL AXI protection.
    # Root debug_map_01 verified this exact bounded loadhw while preserving PC.
    text=f'loadhw -hw {jtag.word(ident["xsa"])} -mem-ranges [list {{0x40400000 0x4040ffff}} {{0x43c00000 0x43c0ffff}}]\n'
    text+=f'set mf [open {jtag.word(path)} w]\n'
    text+='puts $mf "SCOPED_XSA_RANGES 0x40400000..0x4040ffff 0x43c00000..0x43c0ffff"\n'
    text+='puts $mf "ACCESS_ATTRIBUTES_FROM_XSA_HWH; NO_READ_ONLY_PERMISSION_CLAIM"\n'
    text+='puts $mf [loadhw -list]\nclose $mf\n'
    return text

def layout(core,timer):
    return (MAGIC,2,48,216,24,152,64,262144,21268,2,8,12,16,20,0x43c00000,0x40400000,
            core,0x20000,85920,6942,33,168,166608,timer,61,62,63,29,MAGIC,0,0,0)

def verify_index(root):
    for name,digest in read(root/'artifact_hashes.json').items():
        path=(root/name).resolve()
        require(path.is_relative_to(root.resolve()) and sha(path)==digest,'Changed artifact: '+str(path))

def verify_simulation_model(root,system):
    """Bind the reviewed simulation-only vendor scheduling correction explicitly."""
    model=system['simulation_model'];index=read(root/'artifact_hashes.json')
    require(model['kind']=='vendor_PS_VIP_with_simulation_scheduling_correction'
        and model['module']=='processing_system7_vip_v1_0_21_arb_wr_4'
        and model['changed_request_assignments']==22
        and model['arithmetic_or_synthesized_sources_changed'] is False,'Unreviewed simulation model correction')
    require(model['pristine_sha256']=='36ffdfc49370ad8bab1253affd69421cfb420e809fd201e1cc924d8fdc6f75f1'
        and model['patched_sha256']=='eb6a77973f3bb5ee36a508751a79766eed35008a17478400591f743c7b24547d',
        'Simulation model source identity changed')
    for flavor in ('pristine','patched'):
        name=f'simulation_model/{flavor}/processing_system7_vip_v1_0_vl_rfs.sv'
        require(sha(root/name)==model[flavor+'_sha256']==model['files'].get(name)==index.get(name),
            'Pinned simulation source bytes differ: '+flavor)
    model_file=root/'simulation_model/model.json'
    require(read(model_file)==model and sha(model_file)==index['simulation_model/model.json'],'Simulation model manifest differs')
    for name,digest in model['files'].items():
        path=(root/name).resolve()
        require(path.is_relative_to((root/'simulation_model').resolve()) and index.get(name)==digest==sha(path),'Simulation model file differs: '+name)
    init=Path(model['initfile']).resolve()
    require(init==(root/'simulation_model/local_xsim.ini').resolve()
        and sha(init)==model['initfile_sha256']==index['simulation_model/local_xsim.ini'],'Simulation library mapping differs')
    require(sha(root/'simulation_model/scheduling_correction.diff')==model['diff_sha256']==index['simulation_model/scheduling_correction.diff'],
        'Simulation model diff differs')
    if 'compile_library_isolation' in system:
        isolation=isolation_module(root).verify(root)
        require(isolation==system['compile_library_isolation'],'Run-local compilation library binding differs')
        require(sha(root/'simulation_model/compile_library_isolation.json')==index['simulation_model/compile_library_isolation.json'],
            'Compilation library isolation report changed')
        require(system.get('compile_library_cache_guard',{}).get('status')=='PASS'
            and read(root/'compile_library_cache_guard.json')==system['compile_library_cache_guard'],
            'Installed compilation library cache guard did not pass')
    return model

def identity(out,variant,arm_run,system_run,fixed_evidence=None):
    arm,system=read(arm_run/'build_manifest.json'),read(system_run/'run_manifest.json')
    require(arm['status']==system['status']=='complete' and arm['variant']==system['variant']==variant,'Completed matching build required')
    verify_index(arm_run);verify_index(system_run)
    fixed_mel_init_file=verify_mel_binding(system_run,system)
    simulation_model=verify_simulation_model(system_run,system)
    require(arm['system_run']==system_run.name and arm['system_manifest_sha256']==sha(system_run/'run_manifest.json'),'ARM/system manifest binding')
    core=1 if variant=='fixed' else 2
    abi=dict(version=0x20000,core_id=core,output_format=0x11828 if core==1 else 0x30020,
             contract_tag=0x283fff8a if core==1 else 0xc556a8e8,record_bytes=24,dma_base=0x40400000,core_base=0x43c00000,
             IRQ_F2P=dict(mm2s=0,s2mm=1,core=2))
    require(arm['core_id']==core and system['core_kind']==core and all(system['abi'].get(k)==v for k,v in abi.items()),'Wrong DMA/core ABI')
    for name in ('configuration','actual_DMA_PS_simulation','board_implementation'):
        require(system['validation'][name]=='PASS','System gate failed: '+name)
    require(system['timing']['setup_slack_ns']>=0 and system['timing']['hold_slack_ns']>=0,'System timing closure')
    a=arm['arm'];elf=Path(a['elf']);xsa=Path(a['xsa']);bit=system_run/'design'/f'mfcc_dma_{variant}.bit';init=arm_run/'ps7_init.tcl'
    require(elf.resolve().is_relative_to(arm_run.resolve()) and xsa.resolve()==(system_run/'design'/f'mfcc_dma_{variant}.xsa').resolve(),'ELF/XSA belongs to wrong run')
    for name,path,digest in [('xsa',xsa,a['xsa_sha256']),('bitstream',bit,a['bitstream_sha256'])]:
        require(sha(path)==digest==system['outputs'][name]['sha256'],'XSA/bit binding: '+name)
    require(sha(elf)==a['elf_sha256'] and sha(init)==a['ps7_init_sha256'],'ELF/PS init changed')
    cfg=xsa_configuration(xsa)
    require(cfg['ps7_init']==init.read_bytes() and cfg['bitstream_sha256']==sha(bit),'Embedded XSA content binding')
    binary=Elf(elf)
    for name,value in a['symbols'].items():require(binary.symbols[name]==value,'ELF symbol mismatch '+name)
    for name,digest in arm['sources'].items():require(sha(arm_run/'source'/name)==digest,'Compiled snapshot changed '+name)
    xp=(Path(a['bsp_include'])/'xparameters.h').read_text()
    cpu=int(re.search(r'#define XPAR_CPU_CORTEXA9_0_CPU_CLK_FREQ_HZ\s+(\d+)',xp)[1]);timer=cpu//2
    expected_layout=layout(core,timer);raw_layout=binary.at_symbol('arm_dma_layout')
    require(struct.unpack('<32I',raw_layout)==expected_layout,'Actual ELF layout mismatch')
    mmu=binary.symbols['MMUTable'];require(mmu['address']%16384==0 and 0x100000<=mmu['address']<0x2100000-16384,'MMUTable location')
    bundle=references.load(variant)
    require(binary.at_symbol('dma_smoke_pcm')==bundle['pcm'][:1024] and binary.at_symbol('dma_expected')==bundle['records'],'Embedded reference differs')
    require(arm['vectors']['reference']==bundle['metadata'],'Reference provenance differs')
    freeze=read(references.fp32.GOLD/'freeze.json')
    wanted={k:v for k,v in freeze['source_hashes'].items() if k.startswith('hardware/fp32/rtl/')}
    require(len(wanted)==6 and all(system['source_sha256'].get(k)==v for k,v in wanted.items()),'Frozen FP32 arithmetic changed')
    require(system['baseline']['continuous_index_sha256']==references.fp32.GOLD_INDEX_SHA
        and system['baseline']['ip_manifest_sha256']==freeze['ip_manifest_sha256']
        and system['source_sha256']['verification/c/tolerances.json']==references.fp32.TOLERANCE_SHA,'Frozen FP32 IP/tolerance binding')
    for name,digest in read(references.fp32.GOLD/'artifact_manifest.json').items():
        if name.startswith('coefficients/') and name.endswith('.mem'):require(sha(system_run/name)==digest,'Coefficient ROM changed: '+name)
    fixed_manifest=BUILD/'fixed_full_rtl/full_616_repro_20261004_07/run_manifest.json'
    fixed_pin='6b29205e6d077682c5e5484c3021ef1366ace357fa07208e952c70e3640a1325'
    require(sha(fixed_manifest)==fixed_pin and system['baseline']['fixed_manifest_sha256']==fixed_pin
        and system['baseline']['fixed_contract_sha256']==references.CONTRACT_SHA,'Fixed arithmetic baseline changed')
    fixed_sources={k:v for k,v in read(fixed_manifest)['source_sha256'].items() if k.startswith('hardware/fixed/') and Path(k).suffix in ('.sv','.mem')}
    if fixed_evidence is None:
        require('fixed_evidence' not in system,'Optimized fixed system requires explicit --fixed-evidence')
        require(fixed_sources and all(system['source_sha256'].get(k)==v for k,v in fixed_sources.items()),'Frozen fixed arithmetic/ROM changed')
    else:
        require(variant=='fixed','Optimized fixed evidence is only supported for fixed systems')
        candidate=verify_fixed_evidence(fixed_evidence)
        pinned=verify_fixed_evidence(system_run/'provenance/fixed_evidence',system_run/'source')
        require(candidate==pinned==system.get('fixed_evidence'),'Validated fixed candidate evidence binding differs')
        actual={k:v for k,v in system['source_sha256'].items() if k.startswith('hardware/fixed/') and Path(k).suffix in ('.sv','.mem')}
        require(actual==candidate['source_sha256'],'System fixed source manifest differs from validated candidate')
    symbols={**a['symbols'],'MMUTable':mmu}
    ident=dict(variant=variant,core_id=core,arm_run=str(arm_run),system_run=str(system_run),
        arm_manifest_sha256=sha(arm_run/'build_manifest.json'),system_manifest_sha256=sha(system_run/'run_manifest.json'),
        elf=str(elf),xsa=str(xsa),bit=str(bit),ps7_init=str(init),elf_sha256=sha(elf),xsa_sha256=sha(xsa),
        bit_sha256=sha(bit),ps7_init_sha256=sha(init),symbols=symbols,actual_elf_layout=list(expected_layout),
        cpu_bsp_hz=cpu,timer_hz=timer,expected_clocks=expected_configuration(xsa,init),
        mmio_descriptor_address=mmu['address']+(0x43c00000>>20)*4,required_mmio_descriptor=0x43c00c16,
        dma_descriptor_address=mmu['address']+(0x40400000>>20)*4,required_dma_descriptor=0x40400c16,
        hwh_dma=cfg['parameters'],platform=a['platform'],reference=bundle['metadata'],simulation_model=simulation_model,
        debugger_peripheral_maps=peripheral_maps(xsa),
        compiled_source_hashes=arm['sources'],system_source_hashes=system['source_sha256'],fixed_mel_init_file=fixed_mel_init_file,timing_boundary=BOUNDARY,startup_policy=STARTUP)
    if fixed_evidence is not None:ident['fixed_evidence']=candidate
    for name,data in [('development_pcm.bin',bundle['pcm']),('expected_records.bin',bundle['records']),('elf_layout.bin',raw_layout)]:
        (out/name).write_bytes(data)
    dump(out/'identity.json',ident);dump(out/'reference_identity.json',bundle['metadata'])
    return ident,bundle

def decode_stats(data):
    require(len(data)==152,'Statistics length')
    elapsed,cycles,wfi=struct.unpack_from('<3Q',data)
    return dict(elapsed_ticks=elapsed,hardware_busy_cycles=cycles,wfi_ticks=wfi,**dict(zip(STAT_NAMES,struct.unpack_from('<32I',data,24))))

def decode_status(data):
    require(len(data)==216,'Status length')
    return dict(zip(HEADER_NAMES,struct.unpack_from('<IIIi12I',data)))|{'transport':decode_stats(data[64:])}

def decode_trials(data):
    require(len(data)%168==0,'Trials length')
    return [dict(transport=decode_stats(data[i:i+152]),**dict(zip(['result','mismatches','discarded','sequence'],struct.unpack_from('<i3I',data,i+152)))) for i in range(0,len(data),168)]

def check_stats(s,samples,timer,pl,timeout_ms):
    errors=[];frames=0 if samples<512 else 1+(samples-512)//160;records=frames*13
    want=dict(samples=samples,frames=frames,tx_bytes=samples*2,rx_bytes=records*24,core_status=2,core_error_flags=0,
      core_error_detail=0,input_received=samples,input_consumed=samples,output_captured=records,output_sent=records,
      input_bytes=samples*2,output_bytes=records*24,rx_actual_bytes=records*24,irq_tx_count=int(samples>0),
      irq_rx_count=int(frames>0),irq_core_count=1,irq_timer_count=0,irq_tx_status=0x1000 if samples else 0,
      irq_rx_status=0x1000 if frames else 0,irq_core_status=1,events=4|int(samples>0)|(2 if frames else 0),
      deadline_fired=0,recovery_attempted=0,recovery_failed=0,failed_offset=0xffffffff,reserved0=0,reserved1=0,reserved2=0)
    for name,value in want.items():
        if s[name]!=value:errors.append(f'{name}: {s[name]} != {value}')
    for name in ('tx_dma_status','rx_dma_status'):
        if s[name]&0x770 or not s[name]&2:errors.append(name+' DMA error/not idle')
    if not 0<s['elapsed_ticks']<timer*timeout_ms/1000:errors.append('Elapsed ticks outside deadline')
    if not 0<s['hardware_busy_cycles']:errors.append('Missing positive hardware busy cycles')
    if not 0<s['wfi_count']<=1000000 or not 0<s['wfi_ticks']<=s['elapsed_ticks']:errors.append('Actual WFI evidence invalid')
    if s['hardware_busy_cycles']/pl>s['elapsed_ticks']/timer+10/timer+2/pl:errors.append('PL busy exceeds enclosing interval')
    return errors

def compare_job(status,trials,data,history,bundle,mode,sequence,timer,pl,timeout_ms):
    errors=[];samples=512 if mode==0 else 85920;count=13 if mode==0 else 6942;calls=33 if mode==2 else 1
    wanted=dict(magic=MAGIC,version=2,state=3,result=0,mode=mode,sequence=sequence,samples=samples,frames=count//13,
        records=count,completed=calls,warmups=3 if mode==2 else 0,repeats=30 if mode==2 else 1,
        pcm_crc32=zlib.crc32(bundle['pcm'][:samples*2]),numeric_checked=count*calls,numeric_mismatches=0,core_id=bundle['metadata']['core_id'])
    for name,value in wanted.items():
        if status[name]!=value:errors.append(f'status.{name}: {status[name]} != {value}')
    if len(data)!=count*24:errors.append('Result size')
    if len(trials)!=calls:errors.append('Trial count')
    if mode==2 and len(history)!=33*166608:errors.append('History size')
    comparisons=[]
    for i,t in enumerate(trials):
        e=check_stats(t['transport'],samples,timer,pl,timeout_ms)
        for name,value in [('result',0),('mismatches',0),('discarded',int(mode==2 and i<3)),('sequence',i)]:
            if t[name]!=value:e.append('trial '+name)
        raw=history[i*166608:(i+1)*166608] if mode==2 else data
        numerical=None
        try:numerical=references.compare(raw,bundle)
        except (ValueError,struct.error) as ex:e.append(str(ex))
        if numerical and not numerical['passed']:e.append('Bitwise/metadata/numerical comparison')
        comparisons.append(dict(index=i,passed=not e,errors=e,numeric=numerical))
        errors += [f'trial{i}: '+x for x in e]
    if trials and status['transport']!=trials[-1]['transport']:errors.append('Last status/trial mismatch')
    if mode==2 and data!=history[-166608:]:errors.append('Final results/history mismatch')
    return dict(passed=not errors,errors=errors,trials=comparisons,all_outputs_verified=len(comparisons)==calls and not errors)

ENV_REGS={'dma_descriptor.bin':(None,4),'gic_cpu.bin':(0xf8f00100,12),'gic_distributor.bin':(0xf8f01000,4),
 'gic_enable.bin':(0xf8f01100,8),'gic_pending.bin':(0xf8f01200,8),'gic_active.bin':(0xf8f01300,8),
 'gic_spi_priority.bin':(0xf8f0143c,4),'gic_timer_priority.bin':(0xf8f0141c,4),'gic_spi_targets.bin':(0xf8f0183c,4),
 'gic_spi_configuration.bin':(0xf8f01c0c,4),'private_timer.bin':(0xf8f00600,16),
 'dma_tx_control_status.bin':(0x40400000,8),'dma_rx_control_status.bin':(0x40400030,8),
 'dma_tx_length.bin':(0x40400028,4),'dma_rx_length.bin':(0x40400058,4),
 'core_identity.bin':(0x43c00000,8),'core_status.bin':(0x43c0000c,8),'core_diagnostics.bin':(0x43c0002c,76)}

def clock_capture(ident,out):
    symbols={k:v['address'] for k,v in ident['symbols'].items()}
    text=base_capture(symbols,out)
    for name,(address,size) in ENV_REGS.items():text+=jtag.memory_file(address if address is not None else ident['dma_descriptor_address'],size,out/name)
    return text

def check_environment(out,ident):
    value=base_clocks(out,ident,dict(timer_hz=ident['timer_hz']))
    word=lambda name:struct.unpack('<I',(out/name).read_bytes())[0]
    require(word('dma_descriptor.bin')==0x40400c16,'DMA MMIO must be Device/XN with PXN clear')
    regs=value['cpu_registers'];require(regs['cpsr']&0x1ff==0x5f,'Expected ARM system mode with IRQ enabled and FIQ masked')
    require(regs['fpscr']==0,'Unexpected FPSCR state')
    control,mask,binary=struct.unpack('<3I',(out/'gic_cpu.bin').read_bytes())
    require(control&1 and mask>0xa0 and word('gic_distributor.bin')&1,'GIC interface disabled/priority masked')
    enable0,enable1=struct.unpack('<2I',(out/'gic_enable.bin').read_bytes())
    require(enable0&(1<<29) and enable1&0xe0000000==0xe0000000,'Required GIC interrupt disabled')
    require((out/'gic_spi_targets.bin').read_bytes()[1:]==b'\x01\x01\x01','PL IRQs not targeted exclusively to CPU0')
    cfg=word('gic_spi_configuration.bin');require(all((cfg>>(2*(irq%16)))&2==0 for irq in (61,62,63)),'PL IRQ must be level-sensitive')
    require((out/'gic_spi_priority.bin').read_bytes()[1:]==b'\xa0\xa0\xa0' and (out/'gic_timer_priority.bin').read_bytes()[1]==0xa0,'IRQ priorities differ')
    load,count,tc,ts=struct.unpack('<4I',(out/'private_timer.bin').read_bytes())
    require(tc&0xff07==0xff00 and ts&1==0,'Private timer must be stopped/ACKed prescaler255 at breakpoint')
    for name in ('gic_pending.bin','gic_active.bin'):
        p0,p1=struct.unpack('<2I',(out/name).read_bytes())
        require(p0&(1<<29)==0 and p1&0xe0000000==0,'Uncleared DMA/core/deadline '+name)
    value.update(dma_descriptor=word('dma_descriptor.bin'),gic_cpu_control=control,gic_priority_mask=mask,gic_binary_point=binary,
        gic_enable=[enable0,enable1],private_timer=dict(load=load,counter=count,control=tc,status=ts),irq_enabled=True,
        raw_register_files={name:sha(out/name) for name in ENV_REGS})
    return value

def addresses(ident):return {k:v['address'] for k,v in ident['symbols'].items()}

def session_tcl(args,ident,out):
    s=addresses(ident);text=jtag.selection(args.url,args.target_filter,args.cable_serial)
    text+=jtag.reset_initialization(dict(url=args.url,target_filter=args.target_filter,cable_serial=args.cable_serial,ps7_init=ident['ps7_init']))
    text+=f'''set fpga_choices [targets -target-properties -filter {jtag.word(args.fpga_target_filter)}]
if {{[llength $fpga_choices] != 1}} {{error "Expected exactly one selected FPGA"}}
set selected_fpga [lindex $fpga_choices 0]
if {{![dict exists $selected_fpga jtag_cable_serial] || [dict get $selected_fpga jtag_cable_serial] ne {jtag.word(args.cable_serial)}}} {{error "FPGA cable differs"}}
if {{![string match -nocase "*xc7z020*" [dict get $selected_fpga name]]}} {{error "Wrong FPGA part"}}
targets -set [dict get $selected_fpga target_id]
fpga -file {jtag.word(ident['bit'])}
if {{[string trim [fpga -state]] ne "FPGA is configured"}} {{error "FPGA not configured"}}
puts "PROGRAM_CONFIG_STATUS [fpga -config-status]"
targets -set [dict get $chosen target_id]
ps7_post_config
dow {jtag.word(ident['elf'])}
'''
    text+=memory_map_tcl(ident,out/'ready_memory_map.txt')
    text+=guarded_continue(s['arm_dma_ready_breakpoint'],args.timeout,jtag.memory_file(s['arm_dma_status'],216,out/'ready_status.bin'))
    return text+jtag.memory_file(s['arm_dma_layout'],128,out/'layout.bin')+clock_capture(ident,out)+'puts "DMA_SESSION_READY"\nexit\n'

def resume_tcl(args,ident,out):
    s=addresses(ident);text=jtag.selection(args.url,args.target_filter,args.cable_serial)+memory_map_tcl(ident,out/'ready_memory_map.txt')+jtag.require_pc(s['arm_dma_result_breakpoint'])
    text+=f'if {{[mrd -value 0x{s["arm_dma_status"]+8:x}] != 3}} {{error "Prior result not successful"}}\n'
    text+=guarded_continue(s['arm_dma_ready_breakpoint'],args.timeout,jtag.memory_file(s['arm_dma_status'],216,out/'ready_status.bin'))
    return text+jtag.memory_file(s['arm_dma_layout'],128,out/'layout.bin')+clock_capture(ident,out)+'puts "DMA_NEXT_READY_SAME_ELF"\nexit\n'

def input_tcl(args,ident,out,samples):
    s=addresses(ident);text=jtag.selection(args.url,args.target_filter,args.cable_serial)+memory_map_tcl(ident,out/'input_memory_map.txt')+jtag.require_pc(s['arm_dma_ready_breakpoint'])
    text+=f'if {{[mrd -value 0x{s["arm_dma_status"]+8:x}] != 1}} {{error "Lost READY"}}\n'
    text+=f'dow -data {jtag.word(out/"input_pcm.bin")} 0x{s["arm_dma_pcm"]:x}\n'
    return text+jtag.memory_file(s['arm_dma_pcm'],samples*2,out/'pcm_readback.bin')+'puts "INPUT_READBACK_HOST_GATE"\nexit\n'

def execute_tcl(args,ident,out,mode):
    s=addresses(ident);count=13 if mode==0 else 6942;calls=33 if mode==2 else 1
    text=jtag.selection(args.url,args.target_filter,args.cable_serial)+memory_map_tcl(ident,out/'execute_memory_map.txt')+jtag.require_pc(s['arm_dma_ready_breakpoint'])
    text+=f'if {{[mrd -value 0x{s["arm_dma_status"]+8:x}] != 1}} {{error "Lost READY"}}\n'
    text+=f'dow -data {jtag.word(out/"control.bin")} 0x{s["arm_dma_control"]:x}\n'
    diagnostic=jtag.memory_file(s['arm_dma_status'],216,out/'result_status.bin')
    diagnostic+=jtag.memory_file(s['arm_dma_results'],count*24,out/'results.bin')
    diagnostic+=jtag.memory_file(s['arm_dma_trials'],calls*168,out/'trials.bin')
    if mode==2:diagnostic+=jtag.memory_file(s['arm_dma_history'],33*166608,out/'history.bin')
    text+=guarded_continue(s['arm_dma_result_breakpoint'],args.timeout,diagnostic)
    return text+clock_capture(ident,out/'result_environment')+'puts "DMA_RESULT_CAPTURE_COMPLETE"\nexit\n'

def job(args,ident,bundle,mode,sequence,out,capture=None):
    out.mkdir(parents=True);(out/'result_environment').mkdir()
    samples=512 if mode==0 else 85920;pcm=bundle['pcm'][:samples*2];(out/'input_pcm.bin').write_bytes(pcm)
    mark=capture.marker() if capture else 0
    run_script(args,session_tcl(args,ident,out) if mode==0 else resume_tcl(args,ident,out),out/'prepare.tcl')
    ready=decode_status((out/'ready_status.bin').read_bytes());dump(out/'ready_status.json',ready)
    require(ready['magic']==MAGIC and ready['version']==2 and ready['state']==1 and ready['result']==0 and ready['core_id']==ident['core_id'],'Invalid READY or platform initialization failure')
    require(struct.unpack('<32I',(out/'layout.bin').read_bytes())==tuple(ident['actual_elf_layout']),'Runtime layout mismatch')
    clocks=check_environment(out,ident);dump(out/'clocks.json',clocks)
    if capture:capture.require('ARM_DMA_READY_V2',mark)
    run_script(args,input_tcl(args,ident,out,samples),out/'input.tcl')
    require((out/'pcm_readback.bin').read_bytes()==pcm,'PCM readback differs; control not submitted')
    control=(MAGIC,2,mode,samples,args.firmware_timeout_ms,3 if mode==2 else 0,30 if mode==2 else 1,zlib.crc32(pcm),sequence,0,0,0)
    (out/'control.bin').write_bytes(struct.pack('<12I',*control))
    execution_error=None
    try:run_script(args,execute_tcl(args,ident,out,mode),out/'execute.tcl')
    except Exception as ex:execution_error=str(ex)
    require((out/'result_status.bin').exists(),execution_error or 'Missing result status')
    status=decode_status((out/'result_status.bin').read_bytes());dump(out/'result_status.json',status)
    trials=decode_trials((out/'trials.bin').read_bytes()) if (out/'trials.bin').exists() else [];dump(out/'trials.json',trials)
    data=(out/'results.bin').read_bytes() if (out/'results.bin').exists() else b''
    history=(out/'history.bin').read_bytes() if (out/'history.bin').exists() else b''
    comparison=compare_job(status,trials,data,history,bundle,mode,sequence,ident['timer_hz'],clocks['pl_configured_hz'],args.firmware_timeout_ms)
    if execution_error:comparison['passed']=False;comparison['errors'].append(execution_error)
    dump(out/'comparison.json',comparison)
    if mode==2:
        (out/'comparisons').mkdir()
        for trial in comparison['trials']:dump(out/'comparisons'/f'trial_{trial["index"]:03d}.json',trial)
    require(comparison['passed'],'DMA/numeric gate failed: '+str(out/'comparison.json'))
    final=check_environment(out/'result_environment',ident);dump(out/'result_environment/clocks.json',final)
    for name in ('sctlr','fpscr','ttbr0','ttbcr'):
        require(final['cpu_registers'][name]==clocks['cpu_registers'][name],'CPU environment changed: '+name)
    require(final['l2_cache_control']==clocks['l2_cache_control'] and final['global_timer_control']==clocks['global_timer_control'],'Cache/timer changed')
    if capture:capture.require('ARM_DMA_RESULT_V2',mark)
    return dict(path=str(out),passed=True,sequence=sequence,mode=mode,samples=samples,frames=13//13 if mode==0 else 534,
                timer_hz=ident['timer_hz'],pl_configured_hz=clocks['pl_configured_hz'],trials=trials)

def summarize(result):
    trials=result['trials'];timer=result['timer_hz'];pl=result['pl_configured_hz']
    measured=[t for t in trials if not t['discarded']];require(len(measured)==30 and len(trials)==33,'Timing count')
    def stats(values):
        ordered=sorted(values);point=(len(ordered)-1)*.95;low=int(point)
        return dict(median=statistics.median(values),mean=statistics.mean(values),min=min(values),max=max(values),
                    p95=ordered[low]+(ordered[min(low+1,len(ordered)-1)]-ordered[low])*(point-low))
    return dict(measured=True,scope=BOUNDARY,warmups=3,repeats=30,same_elf_session=True,all33_outputs_verified=True,
        clip_ms=stats([t['transport']['elapsed_ticks']*1000/timer for t in measured]),
        hardware_busy_ms=stats([t['transport']['hardware_busy_cycles']*1000/pl for t in measured]),
        wfi_bracket_ms=stats([t['transport']['wfi_ticks']*1000/timer for t in measured]),
        cpu_utilization_measured=False,percentile_method='NumPy default linear interpolation (n-1)*0.95',trials=trials)

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--variant',choices=['fixed','fp32'],required=True);p.add_argument('--run-id',required=True)
    p.add_argument('--arm-run',required=True);p.add_argument('--system-run',required=True)
    p.add_argument('--fixed-evidence',type=Path,help='Opt in to the same completed full616 fixed candidate evidence pinned by the system build')
    p.add_argument('--output-root',type=Path,default=BUILD/'board_validation/dma_board_20261005_01')
    action=p.add_mutually_exclusive_group();action.add_argument('--execute',action='store_true');action.add_argument('--prepare-only',action='store_true')
    p.add_argument('--xsct',type=Path,default=Path('C:/Xilinx/Vitis/2024.2/bin/xsct.bat'));p.add_argument('--url',default='TCP:127.0.0.1:3121')
    p.add_argument('--target-filter');p.add_argument('--fpga-target-filter');p.add_argument('--cable-serial');p.add_argument('--uart-port')
    p.add_argument('--acknowledge-board',choices=['ZYBO_Z7_20']);p.add_argument('--timeout',type=int,default=120)
    p.add_argument('--firmware-timeout-ms',type=int,default=10000);p.add_argument('--skip-timing',action='store_true');p.add_argument('--smoke-only',action='store_true')
    args=p.parse_args(argv)
    if args.fixed_evidence is not None and args.variant!='fixed':p.error('Fixed candidate evidence requires --variant fixed')
    for value in (args.run_id,args.arm_run,args.system_run):
        if not re.fullmatch(r'[A-Za-z0-9_-]+',value):p.error('Use simple new run IDs')
    if not args.output_root.resolve().is_relative_to((BUILD/'board_validation').resolve()):p.error('Evidence must remain under build/board_validation')
    if not 1<=args.firmware_timeout_ms<=60000 or not args.firmware_timeout_ms/1000<args.timeout<=300:p.error('Finite firmware/host timeout required')
    if args.execute and not all([args.target_filter,args.fpga_target_filter,args.cable_serial,args.acknowledge_board]):p.error('Execute requires observed CPU/FPGA filters, cable and board acknowledgement')
    out=args.output_root/args.run_id
    if out.exists():p.error('Refusing to overwrite evidence')
    out.mkdir(parents=True);capture=None
    state=dict(status='PREPARING',variant=args.variant,started_at_utc=datetime.now(timezone.utc).isoformat(),argv=sys.argv,
        board_executed=False,timing_measured=False,evaluation_audio_read=False,timing_boundary=BOUNDARY,startup_policy=STARTUP)
    try:
        deps=[Path(__file__),PROJECT/'scripts/run_board_fp32_accel.py',PROJECT/'scripts/build_arm_dma.py',PROJECT/'scripts/build_arm_fp32_accel.py',PROJECT/'scripts/run_dma_system.py',
              PROJECT/'verification/arm/jtag.py',PROJECT/'verification/arm/clocks.py',PROJECT/'verification/arm/uart.py',
              PROJECT/'verification/arm_dma/dma_references.py',PROJECT/'verification/arm_fp32_accel/references.py']
        shutil.copy2(__file__,out/'runner_snapshot.py');digests={}
        for path in deps:
            name=path.relative_to(PROJECT);dest=out/'runner_dependencies'/name;dest.parent.mkdir(parents=True,exist_ok=True)
            digest=sha(path);shutil.copy2(path,dest);require(sha(dest)==digest==sha(path),'Runner source changed');digests[name.as_posix()]=digest
        dump(out/'runner_dependencies.json',digests)
        ident,bundle=identity(out,args.variant,BUILD/'arm_dma'/args.arm_run,BUILD/'system_dma'/args.system_run,args.fixed_evidence)
        state.update(arm_run=args.arm_run,system_run=args.system_run,preparation_passed=True,reference=bundle['metadata'])
        state['hardware_selection']={name:getattr(args,name) for name in ('url','target_filter','fpga_target_filter','cable_serial','acknowledge_board','uart_port')}
        if not args.execute:
            args.target_filter=args.target_filter or 'name == "Cortex-A9 #0" && jtag_cable_serial == "OBSERVED_SERIAL"'
            args.fpga_target_filter=args.fpga_target_filter or 'name =~ "xc7z020*" && jtag_cable_serial == "OBSERVED_SERIAL"'
            args.cable_serial=args.cable_serial or 'OBSERVED_SERIAL';template=out/'templates';template.mkdir()
            for name,text in [('session',session_tcl(args,ident,template)),('resume',resume_tcl(args,ident,template)),
                              ('input',input_tcl(args,ident,template,85920)),('execute',execute_tcl(args,ident,template,2))]:
                (template/(name+'.tcl')).write_text('# OFFLINE TEMPLATE ONLY\n'+text,encoding='utf-8')
            state['status']='PREPARED_NOT_BOARD_RUN';return 0
        require(args.xsct.is_file(),'XSCT missing');state['xsct']=dict(path=str(args.xsct),sha256=sha(args.xsct))
        if args.uart_port:capture=Capture(args.uart_port,out);capture.start()
        state['board_execution_attempted']=True
        state['smoke']=job(args,ident,bundle,0,1,out/'smoke',capture);state['board_executed']=True
        if args.smoke_only:state['status']='SMOKE_PASS_ONLY_1_FRAME';return 0
        state['development']=job(args,ident,bundle,1,2,out/'development',capture)
        state['development_bit_exact_passed']=True
        if not args.skip_timing:
            state['timing']=job(args,ident,bundle,2,3,out/'timing',capture)
            dump(out/'timing_summary.json',summarize(state['timing']));state['timing_measured']=True
        state['status']='BOARD_DEVELOPMENT_BIT_EXACT_PASS';return 0
    except Exception as ex:
        state.update(status='FAILED',error=str(ex),traceback=traceback.format_exc());print(str(ex),file=sys.stderr);return 1
    finally:
        if capture:capture.close()
        state['completed_at_utc']=datetime.now(timezone.utc).isoformat();dump(out/'run_manifest.json',state)
        dump(out/'artifact_hashes.json',{v.relative_to(out).as_posix():sha(v) for v in sorted(out.rglob('*')) if v.is_file() and v.name!='artifact_hashes.json'})
if __name__=='__main__':raise SystemExit(main())
