"""Offline byte-level positive/negative tests; never substitute for ARM execution."""
from __future__ import annotations
import struct
import numpy as np
import protocol as p
import jtag
from clocks import verify_configuration
from comparison import compare_stages,development_gate


def run_tests():
    records=[]
    def check(name, fn):
        try:
            fn();records.append(dict(name=name,passed=True))
        except Exception as e:
            records.append(dict(name=name,passed=False,error=str(e)))
    def require(value):
        if not value:raise AssertionError('condition false')
    def rejects(fn):
        try:fn()
        except (ValueError,TypeError):return
        raise AssertionError('invalid data was accepted')
    check('frame_boundaries',lambda: require([p.frame_count(n) for n in (0,1,511,512,671,672,85920)]==[0,0,0,1,1,2,534]))
    check('crc32_independent_standard_vector',lambda: require(p.crc32(b'123456789')==0xCBF43926))
    check('control_command_written_last',lambda: require(struct.unpack('<16I',p.control(512,0))[2]==0))
    check('control_trace_no_frame_sentinel',lambda: require(struct.unpack('<16I',p.control(511,0,mode=2,trace_frame=0xFFFFFFFF))[6]==0xFFFFFFFF))
    check('reject_trace_without_existing_frame',lambda: rejects(lambda:p.control(511,0,mode=2,trace_frame=0)))
    check('reject_timing_without_validation',lambda: rejects(lambda:p.control(512,0,mode=3,repeats=30)))
    check('reject_validation_timing_fields',lambda: rejects(lambda:p.control(512,0,repeats=1)))
    check('reject_capacity',lambda: rejects(lambda:p.frame_count(262145)))
    check('reject_missing_symbols',lambda: rejects(lambda:p.parse_nm('00100000 T main')))
    names=p.REQUIRED_SYMBOLS
    nm='\n'.join(f'{0x100000+i*64:08x} T {name}' for i,name in enumerate(names))
    check('parse_elf_symbols',lambda: require(len(p.parse_nm(nm))==len(names)))
    check('reject_mmio_symbol',lambda: rejects(lambda:p.parse_nm(nm.replace('00100000','f8000000'))))
    check('target_guard_requires_identity',lambda: rejects(lambda:jtag.selection('TCP:localhost:3121','','')))
    check('target_guard_exact_serial_and_unique',lambda: require('llength $choices] != 1' in jtag.selection('TCP:localhost:3121','name == "Cortex-A9 #0"','serial') and 'jtag_cable_serial' in jtag.selection('TCP:localhost:3121','name == "Cortex-A9 #0"','serial')))
    check('reject_tcl_injection',lambda: rejects(lambda:jtag.word('x}; mwr 0 0;{')))
    status=[0]*32
    for key,value in dict(magic=p.STATUS_MAGIC,version=1,state=1,error=0,ddr_test_passed=1,control_bytes=64,status_bytes=128).items():
        status[p.STATUS_FIELDS.index(key)]=value
    check('ready_status',lambda: require(p.decode_status(struct.pack('<32I',*status))['state']==1))
    for name,word,value in [('reject_status_error',3,8),('reject_ddr_failure',9,0),('reject_fpscr_fz',14,1<<24)]:
        mutated=status.copy();mutated[word]=value
        check(name,lambda m=mutated:rejects(lambda:p.decode_status(struct.pack('<32I',*m))))
    data=struct.pack('<II13f',0,0,*range(13))
    check('decode_output_records',lambda: require(p.decode_results(data,512)['mfcc'].shape==(1,13)))
    check('empty_output',lambda: require(p.decode_results(b'',511)['mfcc'].shape==(0,13)))
    check('reject_record_order',lambda: rejects(lambda:p.decode_results(struct.pack('<II13f',1,160,*range(13)),512)))
    check('reject_nonfinite_output',lambda: rejects(lambda:p.decode_results(struct.pack('<II13f',0,0,float('nan'),*range(12)),512)))
    layout=dict(magic=p.LAYOUT_MAGIC,version=1,control_bytes=64,status_bytes=128,pcm_capacity=262144,
        output_capacity=2048,result_bytes=60,trace_bytes=7680,trace_start_sample_offset=0,
        trace_frame_id_offset=8,trace_frames_offset=16,trace_windowed_offset=2064,trace_fft_offset=4112,
        trace_power_offset=6168,trace_mel_offset=7196,trace_log_offset=7300,trace_dct_offset=7404,
        trace_mfcc_offset=7456,trace_frame_energy_offset=7508,frame_length=512,fft_bins=257,
        mel_count=26,coefficient_count=13,trace_scalar_bytes=4,trace_id_bytes=8,timing_capacity=100,timing_record_bytes=16)
    def packed(d):return struct.pack('<32I',*(d.get(k,0) for k in p.LAYOUT_FIELDS))
    check('layout_descriptor',lambda: require(p.decode_layout(packed(layout))['trace_fft_offset']==4112))
    check('reject_trace_overlap',lambda: rejects(lambda:p.decode_layout(packed({**layout,'trace_fft_offset':16}))))
    trace=bytearray(layout['trace_bytes']);struct.pack_into('<QQ',trace,0,160,1)
    check('complex_trace_shape_and_ids',lambda: require(p.decode_trace(trace,layout,1)['fft'].shape==(1,257)))
    check('reject_trace_frame_id',lambda: rejects(lambda:p.decode_trace(trace,layout,0)))
    timer=dict(repeats_done=3,warmups_done=3,timer_hz=100,cpu_hz=200,timer_overhead_ticks=1,output_checksum=123,global_timer_control=1)
    raw=b''.join(struct.pack('<4I',ticks,0,1,123) for ticks in (10,20,30))
    check('timing_statistics_64bit',lambda: require(p.timing_statistics(raw,timer,1)['median_seconds']==.2))
    check('reject_timing_checksum',lambda: rejects(lambda:p.timing_statistics(raw,{**timer,'output_checksum':4},1)))
    check('reject_disabled_timer',lambda: rejects(lambda:p.timing_statistics(raw,{**timer,'global_timer_control':0},1)))
    check('reject_timer_prescale',lambda: rejects(lambda:p.timing_statistics(raw,{**timer,'global_timer_control':257},1)))
    check('reject_zero_timer_ticks',lambda: rejects(lambda:p.timing_statistics(struct.pack('<4I',0,0,1,123)*3,timer,1)))
    check('reject_timer_cpu_ratio',lambda: rejects(lambda:p.timing_statistics(raw,{**timer,'cpu_hz':500},1)))
    clock_values=[0x28000,0x20000,0x1E000,0x1F000200]
    expected_clock=dict(registers={name:dict(mask=mask,value=value) for name,mask,value in zip(
        ('ARM_PLL_CTRL','DDR_PLL_CTRL','IO_PLL_CTRL','ARM_CLK_CTRL'),[0x7F011]*3+[0x1F003F30],clock_values)},
        ps_input_configured_hz=33333333.,xsa_cpu_configured_hz=666666687.)
    clock_status=dict(cpu_hz=666666687,timer_hz=333333343)
    check('clock_registers_corroborate_nominal_bsp',lambda:require(verify_configuration(struct.pack('<4I',*clock_values),expected_clock,clock_status)['matched_generated_masks']))
    check('reject_runtime_clock_divisor_change',lambda:rejects(lambda:verify_configuration(struct.pack('<4I',*clock_values[:3],0x1F000300),expected_clock,clock_status)))
    check('reject_nominal_clock_mismatch',lambda:rejects(lambda:verify_configuration(struct.pack('<4I',*clock_values),expected_clock,dict(cpu_hz=800000000,timer_hz=400000000))))
    tolerance={'default':{'atol':.001,'rtol':1e-5},'stages':{}}
    expected={'fft':np.array([[1+2j,3-4j]],dtype=np.complex64)}
    check('complex_comparator_rejects_conjugation',lambda: require(not compare_stages({'fft':np.conj(expected['fft'])},expected,expected,tolerance)['pc']['passed']))
    check('comparator_retains_bit_difference_within_tolerance',lambda: require(
        compare_stages({'mfcc':np.array([[1.00001]],dtype=np.float32)}, {'mfcc':np.array([[1.]])},
            {'mfcc':np.array([[1.]],dtype=np.float32)},tolerance)['pc']['passed'] and
        compare_stages({'mfcc':np.array([[1.00001]],dtype=np.float32)}, {'mfcc':np.array([[1.]])},
            {'mfcc':np.array([[1.]],dtype=np.float32)},tolerance)['pc']['stages']['mfcc']['bit_identical'] is False))
    check('empty_development_never_passes',lambda:require(not development_gate([],{'allowed_numerical_failure_stages':{}})['passed']))
    def fixture_gate(new_mask):
        passed=dict(passed=True,stages={'mfcc':{'passed':True}})
        fail=dict(passed=False,stages={'mfcc':{'passed':False}})
        cases=[dict(id='s'+str(i),role='synthetic',frame_count=0,structural_passed=True,
            comparison={'python':passed,'pc':passed},traces=[]) for i in range(16)]
        cases.append(dict(id='known',role='synthetic',frame_count=1,structural_passed=True,
            comparison={'python':fail,'pc':passed},traces=[dict(python=fail,pc=passed,same_python_violation_masks_as_pc=not new_mask)]))
        cases.append(dict(id='speech',role='development',frame_count=534,structural_passed=True,
            comparison={'python':passed,'pc':passed},traces=[]))
        return development_gate(cases,{'allowed_numerical_failure_stages':{'known':['mfcc']}})
    check('known_failure_is_retained_not_passed',lambda:require(fixture_gate(False)['passed'] and not fixture_gate(False)['numeric_acceptance_passed']))
    check('known_case_id_cannot_hide_new_violation_mask',lambda:require(not fixture_gate(True)['passed']))
    return dict(passed=all(r['passed'] for r in records),count=len(records),checks=records,
        scope='offline ABI/parser/guard fixtures only; no hardware, MFCC execution or elapsed-time measurement')


if __name__=='__main__':
    import json
    result=run_tests();print(json.dumps(result,indent=2));raise SystemExit(0 if result['passed'] else 1)
