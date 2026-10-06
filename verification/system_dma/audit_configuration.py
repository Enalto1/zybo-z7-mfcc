"""Read-only PS/DMA/address/IRQ/core-variant configuration gate for ABI2.

The original PS-only parameter report is pinned. Every unlisted PS property
must be identical, including DDR/MIO/CPU/PLLs. This audit is not board proof.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

BASELINE = Path('D:/2610_MFCC/build/arm_platform/reproduce_01_platform')
BASELINE_SHA = 'f2c701783582624dc07919f55177349cbce365fd72089396618f113a13332160'
TCL = 'hardware/system_dma/create_dma_system.tcl'
PS_EXPECTED = {
    'CONFIG.PCW_USE_M_AXI_GP0': 1,
    'CONFIG.PCW_M_AXI_GP0_FREQMHZ': 100,
    'CONFIG.PCW_EN_CLK0_PORT': 1,
    'CONFIG.PCW_EN_RST0_PORT': 1,
    'CONFIG.PCW_FPGA_FCLK0_ENABLE': 1,
    'CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ': 100,
    'CONFIG.PCW_ACT_FPGA0_PERIPHERAL_FREQMHZ': 100,
    'CONFIG.PCW_CLK0_FREQ': 100000000,
    'CONFIG.PCW_USE_S_AXI_HP0': 1,
    'CONFIG.PCW_S_AXI_HP0_DATA_WIDTH': 64,
    'CONFIG.PCW_S_AXI_HP0_FREQMHZ': 100,
    'CONFIG.PCW_USE_FABRIC_INTERRUPT': 1,
    'CONFIG.PCW_IRQ_F2P_INTR': 1,
    'CONFIG.PCW_NUM_F2P_INTR_INPUTS': 16,
}
PS_ALLOWED = set(PS_EXPECTED) | {
    'CONFIG.PCW_FCLK0_PERIPHERAL_DIVISOR0',
    'CONFIG.PCW_FCLK0_PERIPHERAL_DIVISOR1',
    'CONFIG.PCW_FCLK_CLK0_BUF',
    'CONFIG.Component_Name', 'CUSTOMIZATION_CRC',
}
DMA_EXPECTED = {
    'CONFIG.c_include_sg': 0,
    'CONFIG.c_include_mm2s': 1,
    'CONFIG.c_include_s2mm': 1,
    'CONFIG.c_addr_width': 32,
    'CONFIG.c_sg_length_width': 23,
    'CONFIG.c_m_axi_mm2s_data_width': 64,
    'CONFIG.c_m_axi_s2mm_data_width': 64,
    'CONFIG.c_m_axis_mm2s_tdata_width': 32,
    'CONFIG.c_s_axis_s2mm_tdata_width': 32,
    'CONFIG.c_mm2s_burst_size': 16,
    'CONFIG.c_s2mm_burst_size': 16,
    'CONFIG.c_include_mm2s_dre': 1,
    'CONFIG.c_include_s2mm_dre': 1,
    'CONFIG.c_include_mm2s_sf': 1,
    'CONFIG.c_include_s2mm_sf': 1,
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def need(ok, message):
    if not ok:
        raise ValueError(message)


def table(path, header):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream, delimiter='\t')
        need(reader.fieldnames == header, 'Invalid report columns: '+str(path))
        rows = list(reader)
    need(all(None not in r and all(v is not None for v in r.values()) for r in rows),
         'Malformed report row: '+str(path))
    return rows


def properties(path):
    rows = table(path, ['property', 'value'])
    need(len({r['property'] for r in rows}) == len(rows), 'Duplicate property: '+str(path))
    return {r['property']: r['value'] for r in rows}


def number_matches(value, expected):
    try:
        return Decimal(value) == Decimal(expected)
    except (InvalidOperation, TypeError):
        return False


def compare_ps(before, after):
    failures, changes = [], []
    if before.keys() != after.keys():
        failures.append({'property_set': {'missing': sorted(before.keys()-after.keys()),
                                          'added': sorted(after.keys()-before.keys())}})
    for key in sorted(before.keys() & after.keys()):
        if before[key] != after[key]:
            item = dict(property=key, before=before[key], after=after[key], allowed=key in PS_ALLOWED)
            changes.append(item)
            if key not in PS_ALLOWED:
                failures.append(item)
    for key, value in PS_EXPECTED.items():
        if not number_matches(after.get(key), value):
            failures.append(dict(property=key, expected=value, actual=after.get(key)))
    for key, expected in {
        'VLNV': 'xilinx.com:ip:processing_system7:5.5',
        'CONFIG.Component_Name': 'zybo_dma_processing_system7_0_0',
        'CONFIG.PCW_FCLK0_PERIPHERAL_CLKSRC': 'IO PLL',
        'CONFIG.PCW_IRQ_F2P_MODE': 'DIRECT',
    }.items():
        if after.get(key) != expected:
            failures.append(dict(property=key, expected=expected, actual=after.get(key)))
    try:
        divisors = [Decimal(after[f'CONFIG.PCW_FCLK0_PERIPHERAL_DIVISOR{i}']) for i in (0, 1)]
        valid = all(v == v.to_integral_value() and 1 <= v <= 63 for v in divisors)
        valid &= Decimal(after['CONFIG.PCW_IO_IO_PLL_FREQMHZ']) == 100*divisors[0]*divisors[1]
    except (KeyError, InvalidOperation):
        valid = False
    if not valid:
        failures.append('FCLK divisors must produce100MHz from unchanged IO PLL')
    if after.get('CONFIG.PCW_FCLK_CLK0_BUF') not in ('TRUE', 'FALSE'):
        failures.append('Invalid FCLK buffer Boolean')
    if not re.fullmatch(r'[a-fA-F0-9]{8}', after.get('CUSTOMIZATION_CRC', '')):
        failures.append('Invalid PS customization checksum')
    preserved = {k: v for k, v in before.items() if k not in PS_ALLOWED}
    digest = hashlib.sha256(json.dumps(preserved, sort_keys=True).encode()).hexdigest()
    return dict(status='FAIL' if failures else 'PASS', failures=failures, changes=changes,
                properties=len(after), unchanged_required_properties=len(preserved),
                baseline_unchanged_subset_sha256=digest)


def check_dma(values):
    failures = [dict(property=k, expected=v, actual=values.get(k))
                for k, v in DMA_EXPECTED.items() if not number_matches(values.get(k), v)]
    for key, value in {'VLNV': 'xilinx.com:ip:axi_dma:7.1',
                       'CONFIG.Component_Name': 'zybo_dma_axi_dma_0_0'}.items():
        if values.get(key) != value:
            failures.append(dict(property=key, expected=value, actual=values.get(key)))
    # Vivado revisions expose these optional-mode switches under differing
    # names; if exported they may never enable Micro DMA or async clocks.
    for key in ('CONFIG.c_micro_dma', 'CONFIG.c_prmry_is_aclk_async'):
        if key in values and not number_matches(values[key], 0):
            failures.append(dict(property=key, expected=0, actual=values[key]))
    return dict(status='FAIL' if failures else 'PASS', failures=failures,
                audited_settings={k: values.get(k) for k in DMA_EXPECTED},
                input_max_bytes=524288, output_max_bytes=510432,
                length_field_max_bytes=(1 << 23)-1)


def check_addresses(rows):
    # get_bd_addr_segs also exports slave aperture definitions. They have no
    # assigned OFFSET; validate their identities separately, never treat them
    # as mappings or silently skip arbitrary incomplete rows.
    apertures = {
        ('axi_dma_0/S_AXI_LITE', 'axi_dma_0/S_AXI_LITE/Reg'): 0x1000,
        ('mfcc_dma_0/S_AXI', 'mfcc_dma_0/S_AXI/reg0'): 0x10000,
        ('processing_system7_0/S_AXI_HP0',
         'processing_system7_0/S_AXI_HP0/HP0_DDR_LOWOCM'): 0x40000000,
    }
    normalized, definitions = [], set()
    for row in rows:
        space, segment = row['address_space'].strip('/'), row['segment'].strip('/')
        size = int(row['range'], 0)
        if row['offset'] == '':
            identity = (space, segment)
            need(identity in apertures and size == apertures[identity]
                 and identity not in definitions, 'Invalid slave aperture definition: '+segment)
            definitions.add(identity)
        else:
            normalized.append(dict(space=space, segment=segment,
                                   offset=int(row['offset'], 0), size=size))
    need(len(normalized) == 4, 'Expected exactly four actual address mappings')
    for token, base in (('mfcc_dma_0', 0x43c00000), ('axi_dma_0', 0x40400000)):
        selected = [r for r in normalized if r['space'] == 'processing_system7_0/Data'
                    and token in r['segment']]
        need(len(selected) == 1 and selected[0]['offset'] == base and selected[0]['size'] == 0x10000,
             'Incorrect unique control address mapping: '+token)
    for name in ('Data_MM2S', 'Data_S2MM'):
        selected = [r for r in normalized if r['space'] == 'axi_dma_0/'+name]
        need(len(selected) == 1 and 'HP0_DDR_LOWOCM' in selected[0]['segment']
             and selected[0]['offset'] == 0 and selected[0]['size'] == 0x20000000,
             'Incorrect DMA HP0 DDR mapping: '+name)
    return normalized


def check_irqs(rows):
    actual = [set(r['pins'].replace('{', '').replace('}', '').split()) for r in rows]
    actual = [{pin.lstrip('/') for pin in pins} for pins in actual]
    expected = [set(x) for x in (
        ('axi_dma_0/mm2s_introut', 'irq_concat/In0'),
        ('axi_dma_0/s2mm_introut', 'irq_concat/In1'),
        ('mfcc_dma_0/irq', 'irq_concat/In2'),
        ('irq_concat/dout', 'processing_system7_0/IRQ_F2P'),
    )]
    need(len(actual) == 4 and all(actual.count(pair) == 1 for pair in expected),
         'Actual IRQ nets do not match MM2S/S2MM/core bits0/1/2 to PS')
    need(len({r['net'] for r in rows}) == 4, 'IRQ nets must be distinct')
    return [sorted(pair) for pair in actual]


def audit(run, baseline=BASELINE):
    run, baseline = Path(run).resolve(), Path(baseline).resolve()
    result = dict(status='FAIL', scope='Configuration only; no hardware/GIC execution proof',
                  physical_board_accessed=False, failures=[])
    try:
        need(sha(baseline/'reports/ps7_parameters.tsv') == BASELINE_SHA, 'Changed pinned PS baseline')
        manifest = read(run/'run_manifest.json')
        kind = {'fixed': 1, 'fp32': 2}.get(manifest['variant'])
        need(kind is not None and manifest['core_kind'] == kind, 'Variant/core manifest mismatch')
        need(sha(run/'source'/TCL) == manifest['source_sha256'][TCL], 'System Tcl snapshot changed')
        reports = run/'reports'
        ps = compare_ps(properties(baseline/'reports/ps7_parameters.tsv'), properties(reports/'ps7_parameters.tsv'))
        dma = check_dma(properties(reports/'dma_parameters.tsv'))
        accel = properties(reports/'accelerator_parameters.tsv')
        need(number_matches(accel.get('CONFIG.CORE_KIND'), kind), 'Actual packaged core selection mismatch')
        need(accel.get('VLNV') == 'mfcc.local:user:mfcc_dma:1.0', 'Wrong custom IP VLNV')
        addresses = check_addresses(table(reports/'address_map.tsv', ['address_space', 'segment', 'offset', 'range']))
        irqs = check_irqs(table(reports/'irq_routes.tsv', ['net', 'pins']))
        summary = {}
        for line in (reports/'platform_summary.txt').read_text().splitlines():
            key, value = line.split('=', 1)
            need(key not in summary, 'Duplicate platform summary key')
            summary[key] = value
        for key, expected in {'VIVADO': '2024.2', 'PART': 'xc7z020clg400-1',
                              'BOARD_PART': 'digilentinc.com:zybo-z7-20:part0:1.2',
                              'VARIANT': manifest['variant'], 'CORE_KIND': str(kind),
                              'HP0_DATA_WIDTH': '64', 'VALIDATE_BD_DESIGN': 'passed',
                              'RESET_EXT_ACTIVE_HIGH': '0', 'PHYSICAL_BOARD_ACCESSED': 'false',
                              'INTERRUPTS': 'DMA_MM2S:0,DMA_S2MM:1,CORE_TERMINAL:2',
                              'EXTERNAL_INTERFACES': 'DDR FIXED_IO'}.items():
            need(summary.get(key) == expected, 'Platform summary mismatch: '+key)
        need(number_matches(summary.get('FCLK0_MHZ'), 100), 'Summary clock mismatch')
        original = read(baseline/'platform_manifest.json')
        board = manifest['board_source']
        for key in ('repository', 'commit', 'board_part', 'target_part'):
            need(board[key] == original[key], 'Board provenance mismatch: '+key)
        originals = {Path(item['path']).name: item for item in original['official_sources']}
        for entry in board['files']:
            path = (run/'board_source'/entry['local']).resolve()
            need(path.is_relative_to(run/'board_source'), 'Board source path escape')
            ref = originals[path.name]
            need(sha(path) == entry['sha256'] == ref['sha256'] == sha(ref['path']), 'Board source bytes changed')
        need({Path(item['local']).name for item in board['files']} ==
             {'board.xml', 'part0_pins.xml', 'preset.xml', 'License.txt'}, 'Incomplete board provenance')
        report_files = ['ps7_parameters.tsv', 'dma_parameters.tsv', 'accelerator_parameters.tsv',
                        'address_map.tsv', 'irq_routes.tsv', 'platform_summary.txt']
        digests = {name: sha(reports/name) for name in report_files}
        if (run/'artifact_hashes.json').exists():
            index = read(run/'artifact_hashes.json')
            # During build, an earlier prepared-stage index may not yet
            # contain generated reports. Existing report entries must match.
            for name, digest in digests.items():
                if 'reports/'+name in index:
                    need(index['reports/'+name] == digest, 'Changed indexed report: '+name)
        result.update(ps=ps, dma=dma, variant=manifest['variant'], core_kind=kind,
                      addresses=addresses, irq_routes=irqs, report_sha256=digests,
                      system_tcl_sha256=manifest['source_sha256'][TCL],
                      auditor_sha256=sha(__file__), baseline_sha256=BASELINE_SHA)
        result['failures'] = ps['failures']+dma['failures']
        result['status'] = 'FAIL' if result['failures'] else 'PASS'
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['failures'].append(str(error))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    need(not args.out.exists(), 'Refusing to overwrite an audit')
    result = audit(args.run)
    args.out.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(dict(status=result['status'], failures=result['failures'])))
    return int(result['status'] != 'PASS')


if __name__ == '__main__':
    raise SystemExit(main())
