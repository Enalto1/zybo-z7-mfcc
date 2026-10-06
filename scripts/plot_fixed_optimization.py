"""Plot finalized fixed-optimization board evidence without inventing values.

Input is summary.json from summarize_fixed_optimization.py. All three stages
must have completed board timing and nonnegative post-route timing. Produces
English SVG/PNG/PDF figures and exact plotted CSVs in a fresh build directory.
Synthetic fixtures require an explicit marker AND --synthetic-test; every
result is prominently labeled and cannot be mistaken for new measurements.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, StrMethodFormatter

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT.parent/'build'
STAGES = ('baseline', 'sparse', 'pipelined')
LABELS = {'baseline': 'Baseline', 'sparse': 'Sparse only', 'pipelined': 'Pipelined + parallel'}
COLORS = {'baseline': '#4477AA', 'sparse': '#EE7733', 'pipelined': '#228833'}
MEASURED = 'MEASURED_3_WARMUP_30_REPEATS'
CONDITIONS = '85,920 PCM samples; 534 frames; PL 100 MHz; 3 warm-ups + 30 measured repeats.'
NUMERIC_NOTE = 'Fixed numerical acceptance: NOT_ACCEPTED; bit-exactness is a separate result.'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def finite(value, label, positive=False):
    require(isinstance(value, (int, float)) and not isinstance(value, bool), label+' must be numeric')
    require(math.isfinite(value) and (value > 0 if positive else value >= 0), label+' is out of range')
    return value


def validate(summary_path, synthetic_test):
    data = json.loads(summary_path.read_text(encoding='utf-8-sig'))
    require(data.get('schema_version') == 1, 'Expected summarize_fixed_optimization.py schema version 1')
    require(data.get('errors') == [], 'Evidence error list must be present and empty')
    require(data.get('synthetic_test', False) is synthetic_test,
            'Synthetic fixtures require synthetic_test=true and --synthetic-test together')
    if synthetic_test:
        require(str(data.get('synthetic_test_label', '')).startswith('SYNTHETIC TEST'), 'Missing synthetic fixture label')
        require(data.get('fixture_source_summary_sha256'), 'Synthetic fixture must identify its copied baseline source')
    index_path = summary_path.parent/'artifact_hashes.json'
    require(index_path.is_file(), 'Finalized summary must have artifact_hashes.json')
    index = json.loads(index_path.read_text(encoding='utf-8-sig'))
    require(index.get(summary_path.name) == sha(summary_path), 'Summary artifact hash mismatch')
    require(len(data.get('comparison', [])) == 3 and len(data.get('stages', [])) == 3, 'Exactly three candidates required')
    rows = {row['stage']: row for row in data['comparison']}
    stages = {stage['stage']: stage for stage in data['stages']}
    require(set(rows) == set(stages) == set(STAGES), 'Candidates must be baseline, sparse and pipelined')
    for name in STAGES:
        row, stage = rows[name], stages[name]
        board, system = stage.get('board', {}), stage.get('system', {})
        require(row.get('collection_status') == stage.get('collection_status') == 'COLLECTED', name+': evidence not collected')
        require(row.get('timing_status') == board.get('timing_status') == MEASURED, name+': board timing is not MEASURED')
        require(row.get('system_status') == system.get('status') == 'complete', name+': system incomplete')
        require(row.get('arm_status') == stage.get('arm', {}).get('status') == 'complete', name+': ARM build incomplete')
        require(row.get('board_status') == board.get('status') == 'BOARD_DEVELOPMENT_BIT_EXACT_PASS', name+': board bit-exact gate incomplete')
        require(board.get('development_bit_match') is True and board.get('raw_history_bit_match') is True, name+': raw output history unverified')
        require(board.get('samples') == 85920 and board.get('frames') == 534 and board.get('outputs') == 6942, name+': clip dimensions changed')
        require(abs(finite(board.get('pl_configured_hz'), name+': PL clock', True)-100000000) <= 2, name+': PL clock differs')
        require(all(system.get('validation', {}).get(k) == 'PASS' for k in ('configuration','actual_DMA_PS_simulation','board_implementation')),
                name+': system validation incomplete')
        require(row.get('numerical_acceptance') == stage.get('numerical_acceptance') == board.get('numerical_acceptance') == 'NOT_ACCEPTED',
                name+': fixed numerical acceptance must remain separate')
        timing = board.get('clip_ms', {})
        require(timing.get('count') == 30, name+': measured trial count differs')
        for metric in ('min','median','p95','max'):
            key = 'clip_'+metric+'_ms'
            finite(row.get(key), name+': '+key, True)
            require(row[key] == timing.get(metric), name+': comparison and nested timing disagree')
        require(row['clip_min_ms'] <= row['clip_median_ms'] <= row['clip_p95_ms'] <= row['clip_max_ms'], name+': invalid timing order')
        finite(row.get('pl_busy_median_ms'), name+': BUSY median', True)
        require(row['pl_busy_median_ms'] == board.get('hardware_busy_ms', {}).get('median'), name+': BUSY table mismatch')
        for key in ('lut','ff','dsp','bram_tiles'):
            finite(row.get(key), name+': '+key)
            require(row[key] == system.get('resources', {}).get(key), name+': resource table mismatch')
            if key != 'bram_tiles':
                require(int(row[key]) == row[key], name+': noninteger resource count')
        for column, key in (('post_route_setup_slack_ns','setup_slack_ns'),('post_route_hold_slack_ns','hold_slack_ns')):
            finite(row.get(column), name+': '+column)
            require(row[column] == system.get('timing', {}).get(key), name+': timing table mismatch')
    return data, [rows[name] for name in STAGES]


def write_csv(path, rows):
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def decorate_axis(ax, horizontal=False):
    ax.set_axisbelow(True)
    for side in ('top','right'):
        ax.spines[side].set_visible(False)
    ax.spines['left' if horizontal else 'bottom'].set_color('#777777')
    ax.spines['bottom' if horizontal else 'left'].set_color('#777777')
    ax.grid(axis='x' if horizontal else 'y', color='#E2E2E2', linewidth=0.65)
    ax.tick_params(axis='both', length=3, color='#777777')


def footer(fig, lines, synthetic_test):
    body = ('SYNTHETIC TEST: baseline values copied to every candidate; no new measurements.\n' if synthetic_test else '')
    body += '\n'.join(lines)
    fig.text(0.045, 0.025, body, ha='left', va='bottom', fontsize=7.4,
             color='#9C1B1B' if synthetic_test else '#444444', linespacing=1.45)


def title(fig, main, subtitle, synthetic_test):
    if synthetic_test:
        main = 'SYNTHETIC TEST — '+main
    fig.suptitle(main, x=0.045, y=0.975, ha='left', va='top', fontsize=12, fontweight='bold',
                 color='#9C1B1B' if synthetic_test else '#202020')
    fig.text(0.045, 0.905, subtitle, ha='left', va='top', fontsize=9, color='#444444')


def save_figure(fig, output, name, title_text, synthetic_test):
    prefix = 'synthetic_test_' if synthetic_test else ''
    stem = output/(prefix+name)
    for extension in ('svg','png','pdf'):
        metadata = {'Creator': 'plot_fixed_optimization.py; Matplotlib '+matplotlib.__version__, 'Title': title_text}
        if synthetic_test:
            metadata['Title'] = 'SYNTHETIC TEST — '+title_text
        if extension == 'svg':
            metadata['Date'] = None
        elif extension == 'pdf':
            metadata.update(CreationDate=None, ModDate=None)
        elif extension == 'png':
            metadata['Software'] = metadata.pop('Creator')
        fig.savefig(stem.with_suffix('.'+extension), dpi=300, facecolor='white', metadata=metadata)
    plt.close(fig)


def timing_figure(rows, output, synthetic_test):
    fig, ax = plt.subplots(figsize=(7.2,4.0))
    fig.subplots_adjust(left=0.24, right=0.95, bottom=0.29, top=0.80)
    for i, row in enumerate(rows):
        value = row['clip_median_ms']
        ax.barh(i, value, height=0.50, color=COLORS[row['stage']], alpha=0.88)
        ax.errorbar(value, i, xerr=[[value-row['clip_min_ms']], [row['clip_max_ms']-value]],
                    fmt='o', color='#202020', markerfacecolor='white', markersize=4,
                    capsize=4, elinewidth=1.1, markeredgewidth=1)
        ax.annotate(f'{value:.3f}', (row['clip_max_ms'], i), xytext=(9,0), textcoords='offset points',
                    va='center', fontsize=9, color='#202020')
    ax.set_yticks(range(3), [LABELS[r['stage']] for r in rows])
    ax.invert_yaxis()
    ax.set_xlim(0, max(r['clip_max_ms'] for r in rows)*1.21)
    ax.set_xlabel('Whole-clip time (ms)')
    decorate_axis(ax, horizontal=True)
    title(fig, 'Whole-clip DMA completion time', 'Median with min–max whiskers across 30 measured trials; lower is better.', synthetic_test)
    footer(fig, [CONDITIONS, 'Existing DMA/IRQ boundary. PL BUSY includes stalls; it is not pure compute time.', NUMERIC_NOTE], synthetic_test)
    save_figure(fig, output, 'fixed_optimization_time', 'Whole-clip DMA completion time', synthetic_test)


def resources_figure(rows, output, synthetic_test):
    fig, axes = plt.subplots(2,2,figsize=(7.2,5.7))
    fig.subplots_adjust(left=0.10, right=0.97, bottom=0.22, top=0.81, hspace=0.55, wspace=0.30)
    names = [('lut','LUTs'),('ff','Flip-flops'),('dsp','DSP slices'),('bram_tiles','BRAM tiles (36 Kb equivalent)')]
    labels = ['Baseline','Sparse\nonly','Pipelined +\nparallel']
    for ax, (key, label) in zip(axes.flat, names):
        values = [r[key] for r in rows]
        ax.bar(range(3), values, width=0.55, color=[COLORS[r['stage']] for r in rows], alpha=0.88)
        for i,value in enumerate(values):
            text = f'{value:,.1f}' if key=='bram_tiles' and value!=int(value) else f'{int(value):,}'
            ax.annotate(text, (i,value), xytext=(0,5), textcoords='offset points', ha='center', fontsize=8.3)
        ax.set_xticks(range(3), labels, fontsize=8)
        ax.set_ylim(0, max(values)*1.25 if max(values)>0 else 1)
        ax.set_title(label, loc='left', fontsize=9.5, pad=8)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=key!='bram_tiles'))
        ax.yaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}' if key!='bram_tiles' else '{x:g}'))
        decorate_axis(ax)
    title(fig, 'Post-route FPGA resources', 'Total system resources, including common DMA and interconnect.', synthetic_test)
    footer(fig, [CONDITIONS, 'Zynq-7020; Vivado 2024.2; nonnegative post-route setup and hold slack.', NUMERIC_NOTE], synthetic_test)
    save_figure(fig, output, 'fixed_optimization_resources', 'Post-route FPGA resources', synthetic_test)


def throughput_figure(rows, output, synthetic_test):
    fig, ax = plt.subplots(figsize=(7.2,4.0))
    fig.subplots_adjust(left=0.24, right=0.95, bottom=0.29, top=0.80)
    throughput = []
    for i,row in enumerate(rows):
        value, low, high = (534000/row[k] for k in ('clip_median_ms','clip_max_ms','clip_min_ms'))
        throughput.append(high)
        ax.barh(i,value,height=0.50,color=COLORS[row['stage']],alpha=0.88)
        ax.errorbar(value,i,xerr=[[value-low],[high-value]],fmt='o',color='#202020',markerfacecolor='white',
                    markersize=4,capsize=4,elinewidth=1.1,markeredgewidth=1)
        ax.annotate(f'{value:,.1f}',(high,i),xytext=(9,0),textcoords='offset points',va='center',fontsize=9)
    ax.set_yticks(range(3),[LABELS[r['stage']] for r in rows]);ax.invert_yaxis()
    ax.set_xlim(0,max(throughput)*1.24)
    ax.xaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'))
    ax.set_xlabel('Derived amortized throughput (frames/s)')
    decorate_axis(ax,horizontal=True)
    title(fig,'Throughput derived from whole-clip time','534,000 / median clip milliseconds; whiskers use clip max/min; higher is better.',synthetic_test)
    footer(fig,[CONDITIONS,'Whole-clip amortization; not steady-state frame initiation rate.',NUMERIC_NOTE],synthetic_test)
    save_figure(fig,output,'fixed_optimization_throughput','Throughput derived from whole-clip time',synthetic_test)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary',type=Path,required=True,help='Finalized summary.json from summarize_fixed_optimization.py')
    parser.add_argument('--output-dir',type=Path,required=True,help='Fresh output directory under build')
    parser.add_argument('--synthetic-test',action='store_true',help='Only for explicitly marked synthetic fixture summaries')
    args=parser.parse_args(argv)
    source=args.summary.resolve();output=args.output_dir.resolve()
    require(source.is_file(),'Summary not found')
    require(output.is_relative_to(BUILD.resolve()),'Plot output must remain under build')
    require(not output.exists(),'Refusing to overwrite plot output directory')
    require(not source.is_relative_to(output),'Output overlaps summary input')
    data,rows=validate(source,args.synthetic_test)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'axes.labelsize':9,'xtick.labelsize':8,
                         'ytick.labelsize':9,'axes.linewidth':0.7,'pdf.fonttype':42,'ps.fonttype':42,
                         'svg.fonttype':'path','svg.hashsalt':sha(source),'savefig.facecolor':'white'})
    output.mkdir(parents=True)
    prefix='synthetic_test_' if args.synthetic_test else ''
    performance=[];resources=[]
    for row in rows:
        common=dict(stage=row['stage'],label=LABELS[row['stage']],synthetic_test=args.synthetic_test,
                    samples=85920,frames=534,pl_nominal_hz=100000000,warmups=3,measured_repeats=30,
                    numerical_acceptance='NOT_ACCEPTED')
        performance.append(dict(**common,clip_median_ms=row['clip_median_ms'],clip_min_ms=row['clip_min_ms'],
                                clip_p95_ms=row['clip_p95_ms'],clip_max_ms=row['clip_max_ms'],
                                pl_busy_median_ms=row['pl_busy_median_ms'],
                                amortized_ms_per_frame=row['clip_median_ms']/534,
                                derived_frames_per_second=534000/row['clip_median_ms'],
                                derived_frames_per_second_low=534000/row['clip_max_ms'],
                                derived_frames_per_second_high=534000/row['clip_min_ms'],
                                time_whiskers='min_max',throughput_definition='534000 / clip_median_ms; not steady-state II'))
        resources.append(dict(**common,**{k:row[k] for k in ('lut','ff','dsp','bram_tiles','post_route_setup_slack_ns','post_route_hold_slack_ns')}))
    write_csv(output/(prefix+'performance_data.csv'),performance)
    write_csv(output/(prefix+'resource_data.csv'),resources)
    timing_figure(rows,output,args.synthetic_test)
    resources_figure(rows,output,args.synthetic_test)
    throughput_figure(rows,output,args.synthetic_test)
    shutil.copy2(source,output/'source_summary.json')
    shutil.copy2(__file__,output/Path(__file__).name)
    manifest=dict(status='complete',generated_at_utc=datetime.now(timezone.utc).isoformat(),
                  purpose='SYNTHETIC TEST: copied baseline values, not actual optimization results' if args.synthetic_test else 'Finalized measured fixed optimization figures',
                  synthetic_test=args.synthetic_test,source_summary=str(source),source_summary_sha256=sha(source),
                  script_sha256=sha(__file__),matplotlib_version=matplotlib.__version__,python_version=sys.version,
                  command=sys.argv if argv is None else [str(Path(__file__)),*argv],
                  units=dict(time='milliseconds',throughput='derived frames per second',bram='36 Kb equivalent tiles'),
                  uncertainty='min-max measured clip times; inverse endpoints for throughput',
                  throughput='534000/median clip ms; whole-clip amortized rate, not first-result latency or frame initiation rate',
                  busy='PL BUSY includes stalls and is not pure compute time',numerical_acceptance='NOT_ACCEPTED',
                  board_execution_performed=False,
                  artifacts={p.name:sha(p) for p in output.iterdir() if p.is_file()})
    (output/'plot_manifest.json').write_text(json.dumps(manifest,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(dict(output=str(output),status='complete',synthetic_test=args.synthetic_test,figures=3,formats=['svg','png','pdf']),indent=2))
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (ValueError,OSError,KeyError,TypeError) as error:
        print('FAIL: '+str(error),file=sys.stderr)
        raise SystemExit(1)
