"""Compare the full integer candidate with the preserved mixed-precision run.

This does not retune widths, alter the floor, or filter failing inputs.
"""
from pathlib import Path
import argparse,json
import numpy as np

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model-run',type=Path,required=True)
    p.add_argument('--baseline',type=Path,default=Path(r'D:\2610_MFCC\build\fft_precision\prec_04_verified_full_20261004'))
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    manifest=json.loads((args.model_run/'run_manifest.json').read_text())
    rows=[]
    for case in manifest['cases']:
        data=np.load(args.model_run/case['integer_file'])
        cid=case['id'];stem=f'{cid}_t0p975_d20_out20_in16'
        old=np.fromfile(args.baseline/'arrays'/(stem+'_input_q.bin'),dtype='<i8').reshape(-1,512)
        old_s=np.fromfile(args.baseline/'arrays'/(stem+'_shift.bin'),dtype='<i8')
        old_stage=np.load(args.baseline/'arrays'/(stem+'_stages.npz'))
        ref=np.load(args.baseline/'arrays'/f'{cid}_t0p975_A_float64_reference_stages.npz')
        mel=np.ldexp(data['mel_u60'].astype(float),data['mel_exponent'][:,None])
        mask=(mel<=1e-12)&(ref['mel_energies']>1e-12)
        errors=data['mfcc_q24']/2**24-ref['mfcc']
        row={'id':cid,'frames':case['frames'],'changed_fft_input_codes':int(np.count_nonzero(old!=data['fft_input'])),'changed_bfp_shifts':int(np.count_nonzero(old_s!=data['shift_s'])),'max_fft_input_code_change':int(np.max(np.abs(old-data['fft_input']))) if old.size else 0,'mfcc_max_error':float(np.max(np.abs(errors))) if errors.size else 0,'floor_regressions':[]}
        for f,k in zip(*np.nonzero(mask)):
            row['floor_regressions'].append({'frame':int(f),'mel_index':int(k),'reference_mel':float(ref['mel_energies'][f,k]),'candidate_mel':float(mel[f,k]),'integer_mel':int(data['mel_u60'][f,k]),'shift_s':int(data['shift_s'][f]),'mel_exponent':int(data['mel_exponent'][f]),'log_error':float(data['log_q24'][f,k]/2**24-ref['log_mel'][f,k]),'same_mel_as_baseline':bool(mel[f,k]==old_stage['mel_energies'][f,k])})
        rows.append(row)
    result={'model_run':str(args.model_run),'baseline':str(args.baseline),'fft_width_or_log_floor_policy_changed':False,'new_quantization_stages':['preemphasis signed32/F30','window unsigned31/F30 and output signed32/F30','log signed30/F24','DCT coefficients signed31/F30 and output signed40/F24'],'floor_regressions':sum(len(row['floor_regressions']) for row in rows),'all_floor_regressions_have_integer_zero':all(x['integer_mel']==0 for row in rows for x in row['floor_regressions']),'cases':rows}
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='cases'}))

if __name__=='__main__':main()
