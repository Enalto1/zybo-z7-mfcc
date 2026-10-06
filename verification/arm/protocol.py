"""Offline-tested little-endian debugger ABI; no board I/O or inferred C padding."""
from __future__ import annotations
import re
import struct
import zlib
from pathlib import Path
import numpy as np

VERSION = 1
CONTROL_MAGIC, STATUS_MAGIC, LAYOUT_MAGIC = 0x4D464343, 0x41524D53, 0x41524D4C
STATUS_FIELDS = ('magic version state error mode sample_count frame_count input_crc32 '
    'expected_crc32 ddr_test_passed ddr_failure_index ddr_expected ddr_actual '
    'fpscr_before fpscr_active fpscr_after sctlr cpu_hz timer_hz warmups_done '
    'repeats_done trace_valid output_checksum trace_frame timer_overhead_low '
    'timer_overhead_high control_bytes status_bytes cpsr global_timer_control l2_cache_control reserved0').split()
LAYOUT_FIELDS = ('magic version control_bytes status_bytes pcm_capacity output_capacity '
    'result_bytes trace_bytes trace_start_sample_offset trace_frame_id_offset '
    'trace_frames_offset trace_windowed_offset trace_fft_offset trace_power_offset '
    'trace_mel_offset trace_log_offset trace_dct_offset trace_mfcc_offset '
    'trace_frame_energy_offset frame_length fft_bins mel_count coefficient_count '
    'trace_scalar_bytes trace_id_bytes timing_capacity timing_record_bytes '
    'reserved0 reserved1 reserved2 reserved3 reserved4').split()
REQUIRED_SYMBOLS = ('arm_ready_breakpoint', 'arm_result_breakpoint', 'arm_control',
    'arm_status', 'arm_layout', 'arm_pcm', 'arm_results', 'arm_preemphasis',
    'arm_trace', 'arm_timing_records', 'arm_clock_registers')


def frame_count(samples: int) -> int:
    if type(samples) is not int or not 0 <= samples <= 262144:
        raise ValueError('PCM sample count outside protocol capacity')
    return 0 if samples < 512 else 1 + (samples - 512) // 160


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def control(samples: int, expected_crc: int, *, mode: int = 1, trace_frame: int = 0,
            warmups: int = 0, repeats: int = 0, validation_passed: bool = False) -> bytes:
    count = frame_count(samples)
    if mode not in (1, 2, 3) or not 0 <= expected_crc <= 0xFFFFFFFF:
        raise ValueError('Invalid mode or CRC')
    if mode == 2 and not ((count == 0 and trace_frame == 0xFFFFFFFF) or (count > 0 and 0 <= trace_frame < count)):
        raise ValueError('Focused trace frame does not exist')
    if mode != 3 and (warmups != 0 or repeats != 0 or validation_passed):
        raise ValueError('Validation/trace require zero timing fields')
    if mode == 3 and (not 0 <= warmups <= 20 or not 1 <= repeats <= 100):
        raise ValueError('Timing parameters outside protocol capacity')
    if mode == 3 and (not validation_passed or count == 0):
        raise ValueError('Timing requires prior numerical validation and a full frame')
    # Command remains zero until host has verified the full target PCM readback.
    return struct.pack('<16I', CONTROL_MAGIC, VERSION, 0, mode, samples, expected_crc,
                       trace_frame, warmups, repeats, int(validation_passed), *([0] * 6))


def parse_nm(text: str, *, hello: bool = False) -> dict[str, int]:
    symbols = {}
    required = ('arm_ready_breakpoint', 'arm_status', 'arm_layout', 'arm_clock_registers') if hello else REQUIRED_SYMBOLS
    for line in text.splitlines():
        match = re.fullmatch(r'\s*([0-9a-fA-F]+)\s+(?:[0-9a-fA-F]+\s+)?[a-zA-Z]\s+(\S+)\s*', line)
        if match:
            name, address = match[2], int(match[1], 16)
            if name not in required:
                continue
            if name in symbols and symbols[name] != address:
                raise ValueError('Ambiguous ELF symbol: ' + name)
            symbols[name] = address
    missing = set(required) - symbols.keys()
    if missing:
        raise ValueError('ELF missing symbols: ' + ', '.join(sorted(missing)))
    # This PS-only application is linked in DDR, not MMIO or boot ROM.
    if any(not 0x00100000 <= symbols[name] < 0x02100000 for name in required):
        raise ValueError('Required symbol lies outside pinned linker DDR range [0x00100000,0x02100000)')
    return {name: symbols[name] for name in required}


def decode_status(data: bytes, *, done: bool = False, samples: int | None = None,
                  expected_crc: int | None = None) -> dict:
    if len(data) != 128:
        raise ValueError('Status must contain exactly 128 bytes')
    status = dict(zip(STATUS_FIELDS, struct.unpack('<32I', data)))
    if (status['magic'], status['version'], status['control_bytes'], status['status_bytes']) != (STATUS_MAGIC, 1, 64, 128):
        raise ValueError('Status ABI identity mismatch')
    if status['error'] != 0 or status['ddr_test_passed'] != 1 or status['state'] != (3 if done else 1):
        raise ValueError('Firmware state/DDR/error check failed: ' + str(status))
    if samples is not None and (status['sample_count'] != samples or status['frame_count'] != frame_count(samples)):
        raise ValueError('Firmware sample/frame counts differ from expected full-frame policy')
    if expected_crc is not None and (status['input_crc32'] != expected_crc or status['expected_crc32'] != expected_crc):
        raise ValueError('ARM CRC differs from downloaded PCM')
    # RMode=nearest, FZ=0, DN=0. Arithmetic exception flags are recorded separately.
    if status['fpscr_active'] & ((3 << 22) | (1 << 24) | (1 << 25)):
        raise ValueError('FPSCR arithmetic configuration differs from frozen baseline')
    if done and status['fpscr_after'] & ((3 << 22) | (1 << 24) | (1 << 25)):
        raise ValueError('FPSCR arithmetic configuration changed during execution')
    status['cpu_hz_interpretation'] = 'BSP nominal configuration; not measured frequency'
    status['timer_overhead_ticks'] = status['timer_overhead_low'] | (status['timer_overhead_high'] << 32)
    return status


def decode_layout(data: bytes) -> dict:
    if len(data) != 128:
        raise ValueError('Layout must contain exactly 128 bytes')
    layout = dict(zip(LAYOUT_FIELDS, struct.unpack('<32I', data)))
    expected = dict(magic=LAYOUT_MAGIC, version=1, control_bytes=64, status_bytes=128,
        pcm_capacity=262144, output_capacity=2048, result_bytes=60, frame_length=512,
        fft_bins=257, mel_count=26, coefficient_count=13, trace_scalar_bytes=4,
        trace_id_bytes=8, timing_capacity=100, timing_record_bytes=16)
    if any(layout[name] != value for name, value in expected.items()):
        raise ValueError('Firmware layout does not match MFCC protocol v1')
    ranges = []
    for key, size in [('trace_start_sample_offset', 8), ('trace_frame_id_offset', 8),
        ('trace_frames_offset',2048), ('trace_windowed_offset',2048), ('trace_fft_offset',2056),
        ('trace_power_offset',1028), ('trace_mel_offset',104), ('trace_log_offset',104),
        ('trace_dct_offset',52), ('trace_mfcc_offset',52), ('trace_frame_energy_offset',4)]:
        begin = layout[key]
        if begin % (8 if size == 8 else 4) or begin + size > layout['trace_bytes']:
            raise ValueError('Invalid trace offset: ' + key)
        ranges.append((begin, begin + size))
    ranges.sort()
    if any(right[0] < left[1] for left, right in zip(ranges, ranges[1:])):
        raise ValueError('Overlapping trace descriptor fields')
    return layout


def decode_results(data: bytes, samples: int) -> dict[str, np.ndarray]:
    count = frame_count(samples)
    if len(data) != count * 60:
        raise ValueError('Output record byte count mismatch')
    records = np.frombuffer(data, dtype=np.dtype([('frame_id','<u4'),('start_sample','<u4'),('mfcc','<f4',(13,))]))
    if not np.array_equal(records['frame_id'], np.arange(count)) or not np.array_equal(records['start_sample'], np.arange(count) * 160):
        raise ValueError('Output frame ID/start order mismatch')
    if not np.isfinite(records['mfcc']).all():
        raise ValueError('Output contains NaN or Inf')
    return {name: records[name].copy() for name in records.dtype.names}


def result_checksum(records: dict[str, np.ndarray]) -> int:
    value=0x6D666363
    for frame, start, coefficients in zip(records['frame_id'],records['start_sample'],records['mfcc']):
        value ^= int(frame) ^ int(start)
        for bits in np.ascontiguousarray(coefficients,dtype='<f4').view('<u4'):
            value ^= int(bits)
            value = (((value << 5) | (value >> 27)) + 0x9E3779B9) & 0xFFFFFFFF
    return value


def decode_trace(data: bytes, layout: dict, frame: int) -> dict[str, np.ndarray]:
    if len(data) != layout['trace_bytes']:
        raise ValueError('Focused trace byte count mismatch')
    def take(key, dtype, count):
        return np.frombuffer(data, dtype=dtype, count=count, offset=layout[key]).copy()
    if take('trace_frame_id_offset','<u8',1)[0] != frame or take('trace_start_sample_offset','<u8',1)[0] != frame * 160:
        raise ValueError('Focused trace IDs differ from requested frame')
    stages = {}
    for name, key, count, dtype in [('frames','frames',512,'<f4'),('windowed','windowed',512,'<f4'),
        ('fft','fft',257,'<c8'),('power','power',257,'<f4'),('mel_energies','mel',26,'<f4'),
        ('log_mel','log',26,'<f4'),('dct','dct',13,'<f4'),('mfcc','mfcc',13,'<f4'),
        ('frame_energy','frame_energy',1,'<f4')]:
        value = take('trace_' + key + '_offset',dtype,count)
        if not np.isfinite(value).all():
            raise ValueError('Nonfinite focused trace: ' + name)
        stages[name] = value.reshape(1, -1) if name != 'frame_energy' else value
    return stages


def timing_statistics(data: bytes, status: dict, expected_frames: int) -> dict:
    repeats = status['repeats_done']
    if not 1 <= repeats <= 100 or len(data) != repeats * 16 or status['timer_hz'] == 0:
        raise ValueError('Invalid timing record count or timer frequency')
    if status.get('global_timer_control',0) & 0xFF01 != 1:
        raise ValueError('Global timer must be enabled with prescaler zero')
    if status.get('cpu_hz',0)<=0 or status['timer_hz']!=status['cpu_hz']//2:
        raise ValueError('Global timer frequency must match configured CPU/2')
    words = np.frombuffer(data, dtype='<u4').reshape(-1, 4)
    if not np.all(words[:,2] == expected_frames) or not np.all(words[:,3] == status['output_checksum']):
        raise ValueError('Timing repetition frame count/checksum differs')
    ticks = words[:,0].astype(np.uint64) + (words[:,1].astype(np.uint64) << 32)
    if np.any(ticks==0):
        raise ValueError('Zero elapsed ticks cannot establish a running timer')
    seconds = ticks.astype(np.float64) / status['timer_hz']
    return dict(repeats=repeats, warmups=status['warmups_done'], timer_hz=status['timer_hz'],
        raw_ticks=[int(x) for x in ticks], min_seconds=float(seconds.min()),
        median_seconds=float(np.median(seconds)), p95_seconds=float(np.percentile(seconds,95)),
        max_seconds=float(seconds.max()), timer_overhead_ticks=status['timer_overhead_ticks'],
        overhead_subtracted=False, scope='firmware compute loop; excludes JTAG transfer and UART dump')
