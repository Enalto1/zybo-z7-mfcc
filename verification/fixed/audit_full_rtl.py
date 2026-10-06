"""Audit a finished 616-frame MFCC RTL run without changing its evidence.

Example: python -B verification/fixed/audit_full_rtl.py --run RUN --out AUDIT.json
The output must be new and outside the run. PASS means bit/protocol evidence
and constrained 100 MHz OOC implementation pass; it never accepts numerical
accuracy or board timing. Requires NumPy from the project Python environment.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

import numpy as np

PROJECT = Path(__file__).resolve().parents[2]
COUNTS = dict(cases=24, frames=616, front_samples=315392,
              fft_complex_bins=315392, power_bins=158312,
              mel_values=16016, log_values=16016, mfcc_values=8008)
GROUPS = dict(development=1, synthetic=17, extra_synthetic=6)
TB = 'verification/fixed/full_pipeline/tb_mfcc_fixed_top.sv'
SIM_LOG = 'project/fixed_full.sim/sim_1/behav/xsim/simulate.log'
MEMORIES = {'front.mem': ('fft_input', 16), 'power.mem': ('power_u40', 40),
            'mel.mem': ('mel_u60', 60), 'log.mem': ('log_q24', 30),
            'mfcc.mem': ('mfcc_q24', 40), 'shift.mem': ('shift_s', 8)}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def local(base, relative):
    """All manifest-owned relative file references must stay in their bundle."""
    base = Path(base).resolve()
    path = (base / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(base):
        raise ValueError('file reference escapes bundle: ' + str(relative))
    return path


def table_resources(text):
    rows = {}
    for line in text.splitlines():
        cells = [x.strip() for x in line.split('|')[1:-1]]
        if len(cells) >= 2 and re.fullmatch(r'\d+(?:\.\d+)?', cells[1]):
            rows[cells[0].rstrip('*')] = float(cells[1])
    names = {'LUT': 'Slice LUTs', 'FF': 'Slice Registers', 'latches': 'Register as Latch',
             'LUTRAM': 'LUT as Distributed RAM', 'SRL': 'LUT as Shift Register',
             'BRAM_tiles': 'Block RAM Tile', 'RAMB36': 'RAMB36/FIFO',
             'RAMB18': 'RAMB18', 'DSP': 'DSPs'}
    return {key: rows[name] for key, name in names.items()}


def hierarchy_resources(text):
    rows = {}
    for line in text.splitlines():
        cells = [x.strip() for x in line.split('|')[1:-1]]
        if len(cells) == 10 and all(re.fullmatch(r'\d+', x) for x in cells[2:]):
            # Parenthesized rows are exclusive resources, not hierarchy totals.
            if not cells[0].startswith('('):
                rows[cells[0]] = dict(zip(('LUT', 'logic_LUT', 'LUTRAM', 'SRL',
                                           'FF', 'RAMB36', 'RAMB18', 'DSP'), map(int, cells[2:])))
    return rows


class Audit:
    def __init__(self, run):
        self.run = run
        self.checks = 0
        self.failed = []
        self.report = {}
        self.project_files = {}
        self.manifest = read_json(run / 'run_manifest.json')

    def require(self, condition, message):
        self.checks += 1
        if not condition:
            raise ValueError(message)

    def phase(self, name, function):
        try:
            self.report[name] = function()
        except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
            self.failed.append(dict(phase=name, error=str(error)))

    def hashes(self, base, entries):
        self.require(bool(entries), 'empty hash manifest: ' + str(base))
        for relative, expected in entries.items():
            self.require(bool(re.fullmatch(r'[0-9a-f]{64}', expected)), 'invalid SHA-256: ' + relative)
            self.require(sha(local(base, relative)) == expected, 'SHA-256 mismatch: ' + str(base / relative))
        return len(entries)

    def artifacts(self, run, required):
        entries = read_json(run / 'artifact_hashes.json')
        self.require(set(required) <= set(entries), 'unhashed required run evidence: ' + str(set(required) - set(entries)))
        return self.hashes(run, entries)

    def protocol(self, run):
        protocol = read_json(run / 'protocol.json')
        self.require(protocol.get('status') == 'PASS', 'protocol did not pass')
        for key, value in COUNTS.items():
            self.require(protocol.get(key) == value, 'incorrect protocol count: ' + key)
        self.require(protocol.get('mismatches') == 0, 'nonzero or absent mismatch count')
        self.require(protocol.get('reset_partial_pcm') == 173 and protocol.get('reset_fft_inflight') is True,
                     'missing reset coverage')
        self.require(protocol.get('stall_cycles', 0) >= 5000, 'missing long output-stall coverage')
        self.require(protocol.get('cycles', 0) > 0, 'missing simulation cycle count')
        return protocol

    def manifest_and_artifacts(self):
        m = self.manifest
        self.require(m['status'] == 'complete', 'run is not complete')
        self.require(m['full_corpus'] is True and m['frames'] == 616, 'full 616-frame corpus required')
        self.require(len(m['cases']) == 24 and Counter(c['group'] for c in m['cases']) == GROUPS,
                     'development + 17 synthetic + 6 extra cases required')
        self.require(sum(c['frames'] for c in m['cases'] if c['group'] == 'development') == 534,
                     'development corpus must have 534 frames')
        self.require(len({c['id'] for c in m['cases']}) == 24, 'duplicate case ID')
        self.require(m['tool'] == 'Vivado2024.2' and m['part'] == 'xc7z020clg400-1', 'wrong tool or target')
        self.require(m['clock_ns'] == 10 and m['io_delay_ns'] == 2, 'unexpected OOC clock/I/O assumptions')
        self.require(m['board_run'] is False and m['accuracy_accepted'] is False,
                     'board/accuracy status must remain separate')
        protocol = self.protocol(self.run)
        self.require(m['simulation'] == protocol, 'manifest/protocol mismatch')
        count = self.artifacts(self.run, ['run_manifest.json', 'protocol.json', 'clock.xdc', 'cases.txt',
                                        *MEMORIES, 'pcm.mem', 'fft.mem'])
        return dict(manifest_sha256=sha(self.run / 'run_manifest.json'),
                    artifact_manifest_sha256=sha(self.run / 'artifact_hashes.json'),
                    artifacts_verified=count, protocol=protocol)

    def sources(self):
        entries = self.manifest['source_sha256']
        count = self.hashes(self.run / 'source', entries)
        inventory = {p.relative_to(PROJECT).as_posix() for suffix in ('*.sv', '*.mem')
                     for p in (PROJECT / 'hardware/fixed').rglob(suffix)}
        saved = {p for p in entries if p.startswith('hardware/fixed/') and Path(p).suffix in ('.sv', '.mem')}
        self.require(inventory == saved, 'current RTL/coefficient file inventory differs from snapshot')
        current = saved | {TB, 'hardware/fixed/fft/PROVENANCE.json'}
        for relative in current:
            self.require(sha(local(PROJECT, relative)) == entries[relative], 'current RTL/TB differs: ' + relative)
        provenance = read_json(self.run / 'source/hardware/fixed/fft/PROVENANCE.json')
        for entry in provenance['files']:
            self.require(sha(entry['source']) == entry['source_sha256'], 'original FFT changed: ' + entry['source'])
            self.require(sha(local(self.run / 'source/hardware/fixed/fft', entry['copy'])) == entry['copy_sha256'],
                         'FFT work-copy provenance mismatch: ' + entry['copy'])
        return dict(snapshot_files=count, current_RTL_TB_and_provenance_files=len(current),
                    original_FFT_files=len(provenance['files']))

    def contract(self, pin):
        base = Path(pin['path']).resolve()
        self.require(sha(base / 'contract.json') == pin['sha256'], 'C contract pin changed')
        self.require(sha(base / 'PUBLISHED.json') == pin['publication_sha256'], 'publication pin changed')
        publication = read_json(base / 'PUBLISHED.json')
        self.require(publication['status'] == 'PUBLISHED', 'contract is unpublished')
        for name, key in [('contract.json', 'contract_sha256'), ('artifact_hashes.json', 'artifact_manifest_sha256'),
                          ('verification.json', 'verification_sha256')]:
            self.require(sha(base / name) == publication[key], 'contract publication hash: ' + name)
        hashes = read_json(base / 'artifact_hashes.json')
        count = self.hashes(base, hashes)
        contract = read_json(base / 'contract.json')
        verification = read_json(base / 'verification.json')
        self.require(verification['status'] == 'PASS' and verification['cases'] == 24 and verification['frames'] == 616,
                     'contract verifier did not cover all cases')
        self.require(contract['source_run']['sha256'] == self.manifest['model_manifest_sha256'],
                     'contract model pin differs from RTL run')
        self.require(contract['accuracy']['evaluation_audio_used'] is False, 'evaluation audio used')
        self.require(contract['status']['numerical_accuracy'] == 'NOT_ACCEPTED', 'unexpected accuracy acceptance')
        return base, contract, count

    def project_references(self):
        project = self.run / 'project/fixed_full.xpr'
        indexed = read_json(self.run / 'artifact_hashes.json')
        self.require('project/fixed_full.xpr' in indexed, 'unhashed Vivado project')
        tree = ET.parse(project)
        files = {}
        for element in tree.iter('File'):
            reference = element.attrib['Path']
            expanded = reference.replace('$PPRDIR', str(project.parent))
            self.require('$' not in expanded, 'unresolved project path variable: ' + reference)
            path = Path(expanded).resolve()
            self.require(path.is_relative_to(self.run) and path.is_file(),
                         'missing/outside-run project reference: ' + reference)
            relative = path.relative_to(self.run).as_posix()
            self.require(relative in indexed and sha(path) == indexed[relative],
                         'project reference is not authenticated: ' + relative)
            files[relative] = sha(path)
            self.project_files[relative] = path
        self.require(len(files) >= 25, 'incomplete Vivado source project')
        return dict(project=str(project), files=files, references_verified=len(files))

    def model_and_vectors(self):
        model_base = Path(self.manifest['model']).resolve()
        model = read_json(model_base / 'run_manifest.json')
        self.require(sha(model_base / 'run_manifest.json') == self.manifest['model_manifest_sha256'], 'model manifest pin changed')
        self.require(model['status'] == 'complete' and model['total_frames'] == 616 and model['runtime'] == 'integer_only',
                     'model run is incomplete or not integer-only')
        source_count = self.hashes(model_base / 'source_snapshot', model['source_sha256'])
        self.require(sha(model_base / 'coefficients.json') == model['coefficient_sha256'], 'model coefficient pin changed')
        base, contract, contract_count = self.contract(self.manifest['c_contract'])
        for relative, digest in model['source_sha256'].items():
            self.require(sha(local(base / 'model_snapshot', relative)) == digest, 'contract/model snapshot differs: ' + relative)
        self.require(sha(base / 'coefficients/coefficients.json') == model['coefficient_sha256'], 'contract coefficient source differs')
        self.require(len(model['cases']) == len(contract['cases']) == len(self.manifest['cases']) == 24, 'case list length')
        packed = {name: [] for name in [*MEMORIES, 'pcm.mem', 'fft.mem']}
        stage_components = 0

        def array(descriptor):
            path = local(base, descriptor['file'])
            self.require(sha(path) == descriptor['sha256'] and path.stat().st_size == descriptor['bytes'],
                         'contract array descriptor mismatch: ' + descriptor['file'])
            dtype = np.dtype(descriptor['dtype'])
            self.require(dtype.kind in 'iub', 'noninteger contract array')
            return np.fromfile(path, dtype=dtype).reshape(descriptor['shape'])

        for mc, cc, rc in zip(model['cases'], contract['cases'], self.manifest['cases']):
            key = (mc['id'], mc['group'], mc['frames'])
            self.require(key == (cc['id'], cc['group'], cc['frames']) == (rc['id'], rc['group'], rc['frames']),
                         'case identity/order mismatch: ' + mc['id'])
            self.require(rc['samples'] == mc['pcm']['samples'], 'PCM sample count: ' + mc['id'])
            pcm = local(model_base / 'arrays', mc['pcm']['file'])
            npz = local(model_base, mc['integer_file'])
            self.require(sha(pcm) == mc['pcm']['sha256'] and sha(npz) == mc['sha256'], 'model case hash: ' + mc['id'])
            pcm_array = np.fromfile(pcm, dtype='<i2')
            self.require(np.array_equal(pcm_array, array(cc['pcm'])), 'contract PCM differs: ' + mc['id'])
            self.require(pcm_array.size == rc['samples'], 'PCM byte length: ' + mc['id'])
            packed['pcm.mem'].append(pcm_array.astype(np.uint64) & 65535)
            with np.load(npz, allow_pickle=False) as values:
                for name, descriptor in cc['stages'].items():
                    actual = values[name]
                    expected = array(descriptor)
                    self.require(actual.dtype.kind in 'iub' and np.array_equal(actual, expected),
                                 'contract/model stage differs: ' + mc['id'] + '/' + name)
                    stage_components += actual.size
                for name, (key, width) in MEMORIES.items():
                    packed[name].append(values[key].astype(np.uint64).ravel() & ((1 << width) - 1))
                real = values['fft_re'].astype(np.uint64).ravel() & 0xfffff
                imag = values['fft_im'].astype(np.uint64).ravel() & 0xfffff
                packed['fft.mem'].append((real << 20) | imag)
        vector_counts = {}
        for name, rows in packed.items():
            expected = np.concatenate(rows)
            words = (self.run / name).read_text(encoding='ascii').split()
            actual = np.fromiter((int(word, 16) for word in words), dtype=np.uint64, count=len(words))
            self.require(np.array_equal(actual, expected), 'RTL input/expected memory differs from pinned model: ' + name)
            vector_counts[name] = actual.size
        cases = [int(x) for x in (self.run / 'cases.txt').read_text().split()]
        expected_cases = [24, 616] + [v for c in self.manifest['cases'] for v in (c['samples'], c['frames'])]
        self.require(cases == expected_cases, 'cases.txt differs from full manifest')
        # Earlier v2 publications omitted this descriptor; the coefficient file
        # itself was still included in their authenticated artifact manifest.
        twiddle_digest = contract['coefficients'].get('twiddle_q15', {}).get('sha256')
        if twiddle_digest is None:
            twiddle_digest = read_json(base / 'artifact_hashes.json')['coefficients/twiddle_1024_w16.mem']
        # Earlier completed runs lost their root staging copies; the reason
        # was not established. Require persistent authenticated project paths,
        # the actual simulator copies, and successful synthesis read messages.
        # Vivado's optional mem_init cache is also checked when present.
        coefficient_copies = {}
        synthesis_log = (self.run / 'console.log').read_text(encoding='utf-8', errors='replace')
        for name,relative,digest in [
            ('twiddle_1024_w16.mem','hardware/fixed/fft/twiddle_1024_w16.mem',twiddle_digest),
            ('mel_fw16.mem','hardware/fixed/power_mel/mel_fw16.mem',self.manifest['source_sha256']['hardware/fixed/power_mel/mel_fw16.mem'])]:
            source_key = 'source/' + relative
            self.require(source_key in self.project_files,
                         'coefficient is not a persistent project source: ' + name)
            paths = [self.project_files[source_key],
                     self.run/'project/fixed_full.sim/sim_1/behav/xsim'/name]
            # A synth-only reuse run obtains its simulation copy from the
            # authenticated original simulation.
            if self.manifest.get('simulation_source'):
                paths[1]=Path(self.manifest['simulation_source']['path'])/'project/fixed_full.sim/sim_1/behav/xsim'/name
            cache = self.run/'project/fixed_full.ip_user_files/mem_init_files'/name
            if cache.is_file():
                paths.append(cache)
            for path in paths:self.require(sha(path)==digest,'coefficient source/simulator/synthesis identity: '+str(path))
            self.require("$readmem data file '" + name + "' is read successfully" in synthesis_log,
                         'synthesis coefficient read not confirmed: ' + name)
            coefficient_copies[name]=[str(path) for path in paths]
        mel = array(contract['coefficients']['mel_q16']).ravel().tolist()
        table = [int(x, 16) for x in (self.run / 'source/hardware/fixed/power_mel/mel_fw16.mem').read_text().split()]
        self.require(table == mel + [0] * (8192 - len(mel)), 'RTL Mel coefficients/padding')
        return dict(model_path=str(model_base), model_sha256=self.manifest['model_manifest_sha256'],
                    model_source_files=source_count, contract_path=str(base), contract_sha256=self.manifest['c_contract']['sha256'],
                    contract_artifacts=contract_count, model_contract_integer_components=stage_components,
                    memory_words=vector_counts, coefficient_copies=coefficient_copies,cases=24, frames=616)

    def simulation_evidence(self):
        current = self.run
        seen = set()
        chain = []
        wanted = self.manifest
        expected_sources = {k: v for k, v in wanted['source_sha256'].items() if k.startswith(('hardware/', 'verification/'))}
        coefficient_staging = {'twiddle_1024_w16.mem', 'mel_fw16.mem'}
        vector_names = sorted(p.name for p in self.run.glob('*.mem') if p.name not in coefficient_staging) + ['cases.txt']
        while True:
            self.require(current not in seen and len(seen) < 16, 'cyclic/excessive simulation reuse chain')
            seen.add(current)
            m = read_json(current / 'run_manifest.json')
            self.require(m['status'] in ('complete', 'failed'), 'simulation source still running')
            protocol = self.protocol(current)
            self.require(protocol == read_json(self.run / 'protocol.json'), 'reused protocol content changed')
            self.require(m['cases'] == wanted['cases'] and m['model_manifest_sha256'] == wanted['model_manifest_sha256'],
                         'reused cases/model differ')
            sources = {k: v for k, v in m['source_sha256'].items() if k.startswith(('hardware/', 'verification/'))}
            self.require(sources == expected_sources, 'reused RTL/TB/coefficient inventory or hashes differ')
            self.hashes(current / 'source', sources)
            self.require(sorted(p.name for p in current.glob('*.mem') if p.name not in coefficient_staging) + ['cases.txt'] == vector_names, 'reused memory inventory differs')
            for name in vector_names:
                self.require(sha(current / name) == sha(self.run / name), 'reused vector differs: ' + name)
            # A failed implementation may still contain valid completed simulation.
            node = dict(path=str(current), overall_status=m['status'], implementation=m.get('implementation'),
                        error=m.get('error'), manifest_sha256=sha(current / 'run_manifest.json'),
                        protocol_sha256=sha(current / 'protocol.json'))
            chain.append(node)
            reference = m.get('simulation_source')
            if not reference:
                artifact_count = self.artifacts(current, ['run_manifest.json', 'protocol.json', SIM_LOG, *vector_names])
                log = (current / SIM_LOG).read_text(encoding='utf-8', errors='replace')
                self.require('FULL_PIPELINE_PASS' in log, 'missing original simulator PASS marker')
                self.require('FULL_BEGIN cases=24 frames=616' in log, 'simulator did not begin the full corpus')
                rows = [(int(c), int(f)) for c, f in re.findall(r'FULL_CASE_PASS case=(\d+) frames=(\d+) cycles=\d+', log)]
                self.require(rows == [(i, c['frames']) for i, c in enumerate(wanted['cases'])], 'missing/extra/reordered original case PASS records')
                self.require(not re.search(r'(?im)^\s*(?:Fatal:|ERROR:|FATAL:)', log), 'fatal/error in original simulation log')
                node['original_simulator_log_sha256'] = sha(current / SIM_LOG)
                node['original_artifacts_verified'] = artifact_count
                break
            prior = Path(reference['path']).resolve()
            self.require(reference['matched_rtl_tb_and_vectors'] is True, 'reuse match was not recorded')
            self.require(sha(prior / 'run_manifest.json') == reference['manifest_sha256'], 'prior simulation manifest pin changed')
            self.require(sha(prior / 'protocol.json') == reference['protocol_sha256'], 'prior simulation protocol pin changed')
            current = prior
        return dict(reused=len(chain) > 1, chain=chain, original_case_pass_records=24)

    def implementation(self):
        self.require(self.manifest.get('implementation') == 'route', 'post-route implementation required')
        required = ['timing.json', 'timing_route.rpt', 'drc_route.rpt', 'utilization_synth.rpt',
                    'utilization_route.rpt', 'utilization_hier_synth.rpt', 'bram_cells.txt', 'routed.dcp']
        indexed = read_json(self.run / 'artifact_hashes.json')
        self.require(set(required) <= set(indexed), 'missing hashed implementation evidence')
        timing = read_json(self.run / 'timing.json')
        self.require(timing == self.manifest['timing'], 'timing.json/manifest mismatch')
        text = (self.run / 'timing_route.rpt').read_text()
        self.require('Vivado v.2024.2' in text and 'Design State : Routed' in text, 'unexpected timing report tool/state')
        clocks = re.findall(r'(?m)^\s*(\S+)\s+\{[\d.]+\s+[\d.]+\}\s+([\d.]+)\s+([\d.]+)\s*$', text)
        self.require(clocks == [('clk', '10.000', '100.000')], 'routed clock is not the single 100 MHz clock')
        xdc = (self.run / 'clock.xdc').read_text()
        self.require(re.search(r'set_input_delay\s+2\.0+\s+-clock\s+clk', xdc) is not None
                     and re.search(r'set_output_delay\s+2\.0+\s+-clock\s+clk', xdc) is not None,
                     'explicit 2 ns OOC input/output constraints missing')
        self.require(not re.search(r'\bset_(?:false_path|multicycle_path|max_delay|min_delay)\b', xdc),
                     'unexpected timing exceptions in clock.xdc')
        checks = {name: int(count) for name, count in re.findall(r'checking (\w+) \((\d+)\)', text)}
        self.require(len(checks) == 12 and not any(checks.values()), 'missing/violating timing constraints: ' + str(checks))
        summary = re.search(r'WNS\(ns\)[^\n]*\n[^\n]*\n\s*([-\d.]+)\s+([-\d.]+)\s+(\d+)\s+\d+\s+([-\d.]+)\s+([-\d.]+)\s+(\d+)\s+\d+\s+([-\d.]+)', text)
        self.require(summary is not None, 'timing summary table not found')
        wns, tns, setup_fail, whs, ths, hold_fail, pulse = map(float, summary.groups())
        self.require(abs(wns - timing['setup_slack_ns']) < 0.0001 and abs(whs - timing['hold_slack_ns']) < 0.0001,
                     'timing report/slack JSON mismatch')
        self.require(wns >= 0 and whs >= 0 and pulse >= 0 and tns == ths == setup_fail == hold_fail == 0,
                     'routed timing constraints are not met')
        resource = {stage: table_resources((self.run / ('utilization_' + stage + '.rpt')).read_text())
                    for stage in ('synth', 'route')}
        for stage, values in resource.items():
            self.require(values['latches'] == 0, stage + ' inferred latches')
            self.require(values['BRAM_tiles'] == values['RAMB36'] + values['RAMB18'] / 2, stage + ' BRAM accounting')
        mapping = {}
        # Route hierarchy is used when supplied; otherwise explicitly retain the
        # narrower synthesis-mapping claim and compare routed primitive totals.
        routed_mapping = (self.run / 'bram_cells_route.txt').is_file() and (self.run / 'utilization_hier_route.rpt').is_file()
        for stage in ('synth', 'route') if routed_mapping else ('synth',):
            cells_name = 'bram_cells.txt' if stage == 'synth' else 'bram_cells_route.txt'
            hierarchy_name = 'utilization_hier_' + stage + '.rpt'
            self.require(cells_name in indexed and hierarchy_name in indexed, 'unhashed BRAM/hierarchy report')
            cells = (self.run / cells_name).read_text().splitlines()
            hierarchy = hierarchy_resources((self.run / hierarchy_name).read_text())
            counts = Counter(line.split()[-1] for line in cells if line.strip())
            self.require(counts['RAMB36E1'] == resource[stage]['RAMB36'] and counts['RAMB18E1'] == resource[stage]['RAMB18'],
                         stage + ' BRAM cell/summary mismatch')
            for part in ('U_FRONT/U_PRE_RAM/', 'U_FRONT/window_mem', 'u_reorder/', 'U_POWER_RAM/'):
                self.require(any(part in cell for cell in cells), stage + ' missing frame BRAM: ' + part)
            for name in ('U_FRONT', 'U_PRE_RAM', 'u_reorder', 'U_POWER_RAM'):
                self.require(name in hierarchy and hierarchy[name]['LUTRAM'] == 0, stage + ' frame-buffer LUTRAM: ' + name)
            mapping[stage] = dict(cells=cells, frame_hierarchies={name: hierarchy[name] for name in ('U_FRONT', 'U_PRE_RAM', 'u_reorder', 'U_POWER_RAM')})
        if not routed_mapping:
            self.require(all(resource['route'][key] == resource['synth'][key] for key in ('RAMB36', 'RAMB18')),
                         'routed BRAM totals changed without routed hierarchy evidence')
        drc = []
        for line in (self.run / 'drc_route.rpt').read_text().splitlines():
            fields = [x.strip() for x in line.split('|')[1:-1]]
            if len(fields) == 4 and re.fullmatch(r'[A-Z0-9]+-\d+', fields[0]):
                self.require(fields[1] in ('Warning', 'Advisory'), 'DRC error/critical warning: ' + line)
                drc.append(dict(rule=fields[0], severity=fields[1], description=fields[2], count=int(fields[3])))
        found = re.search(r'Checks found:\s*(\d+)', (self.run / 'drc_route.rpt').read_text())
        self.require(found is not None and sum(row['count'] for row in drc) == int(found.group(1)), 'incomplete DRC report')
        return dict(status='PASS', timing_scope='100 MHz OOC internal timing; physical external port/clock placement remains unverified',
                    WNS_ns=wns, WHS_ns=whs, timing_checks=checks, resources=resource,
                    routed_hierarchy_verified=routed_mapping, BRAM_mapping=mapping, DRC=drc,
                    limitations=['OOC external HD.PARTPIN_LOCS and HD.CLK_SRC may be unset; board timing is not accepted',
                                 'OOC DRC omits some top-level connectivity checks'])

    def execute(self):
        for name, function in [('run', self.manifest_and_artifacts), ('sources', self.sources),
                               ('project_references', self.project_references),
                               ('model_contract_vectors', self.model_and_vectors),
                               ('simulation_evidence', self.simulation_evidence), ('implementation', self.implementation)]:
            self.phase(name, function)
        return dict(schema='mfcc-full-rtl-audit-1', status='FAIL' if self.failed else 'PASS',
                    run=str(self.run), audited_at_utc=datetime.now(timezone.utc).isoformat(),
                    auditor_sha256=sha(__file__), checks=self.checks, failures=self.failed,
                    expected_counts=COUNTS, evidence=self.report, numerical_accuracy_accepted=False,
                    board_run=False, evaluation_audio_used=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    run, output = args.run.resolve(), args.out.resolve()
    if output.exists() or output.is_relative_to(run):
        parser.error('--out must be a new path outside the immutable run')
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = Audit(run).execute()
    except (OSError, ValueError, KeyError, TypeError) as error:
        report = dict(schema='mfcc-full-rtl-audit-1', status='FAIL', run=str(run),
                      failures=[dict(phase='load', error=str(error))], auditor_sha256=sha(__file__))
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps(dict(status=report['status'], audit=str(output), checks=report.get('checks', 0),
                          failures=report['failures']), indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
