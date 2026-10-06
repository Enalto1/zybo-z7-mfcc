"""Build/replay integer frontend and log/DCT unit transactions in Vivado2024.2."""
from pathlib import Path
import argparse,json,subprocess,sys,hashlib,shutil
import numpy as np
from fractions import Fraction
import random
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from software.fixed_model.full_integer import log_integer

FRONT_TB=r'''
`timescale 1ns/1ps
module tb_front;
logic clk=0,rst_n=0,start=0,pvalid=0,pready,plast=0,valid,ready=0,last,error,done;
logic signed[15:0] pcm=0,fft;
logic[31:0] frame;logic[8:0] idx;logic signed[7:0] bfp;
integer cycles=0,seen=0,done_count=0,fin,fout,rc,cases,length,sample,j,c;
integer exfft,exframe,exidx,exs,exlast,exerror;
logic stalled=0;logic[67:0] held;
always #5 clk=~clk;
mfcc_fixed_frontend U_DUT(.clk(clk),.rst_n(rst_n),.i_clip_start(start),.i_pcm_valid(pvalid),.o_pcm_ready(pready),.i_pcm(pcm),.i_pcm_last(plast),.o_valid(valid),.i_ready(ready),.o_fft(fft),.o_frame(frame),.o_index(idx),.o_bfp_s(bfp),.o_last(last),.o_error(error),.o_clip_done(done));
always @(negedge clk) begin
 cycles=cycles+1; ready=(cycles%19>=5)&&!(cycles>=20000&&cycles<20300);
 if(cycles>10000000) $fatal(1,"front timeout");
end
always @(posedge clk) if(rst_n) begin
 if(done) done_count=done_count+1;
 if(stalled && {valid,fft,frame,idx,bfp,last,error}!==held) $fatal(1,"front stall unstable");
 stalled=valid&&!ready;held={valid,fft,frame,idx,bfp,last,error};
 if(valid&&ready) begin
  rc=$fscanf(fout,"%d %d %d %d %d %d\n",exfft,exframe,exidx,exs,exlast,exerror);
  if(rc!=6) $fatal(1,"front unexpected output %d",seen);
  if($signed(fft)!=exfft || frame!=exframe || idx!=exidx || $signed(bfp)!=exs || last!=exlast || error!=exerror)
   $fatal(1,"front mismatch n=%d got=%d/%d/%d/%d/%d/%d expected=%d/%d/%d/%d/%d/%d",seen,$signed(fft),frame,idx,$signed(bfp),last,error,exfft,exframe,exidx,exs,exlast,exerror);
  seen=seen+1;
 end
end
initial begin
 fin=$fopen("front_input.txt","r");fout=$fopen("front_expected.txt","r");
 if(!fin||!fout) $fatal(1,"files");
 repeat(4) @(negedge clk);rst_n=1;
 // Reset drops a partial clip and previous-sample state.
 start=1;@(negedge clk);start=0;pvalid=1;pcm=-32768;
 @(negedge clk);pvalid=0;rst_n=0;repeat(3) @(negedge clk);rst_n=1;
 rc=$fscanf(fin,"%d\n",cases);
 for(c=0;c<cases;c=c+1) begin
  rc=$fscanf(fin,"%d\n",length);start=1;plast=(length==0);@(negedge clk);start=0;plast=0;
  for(j=0;j<length;j=j+1) begin
   rc=$fscanf(fin,"%d\n",sample);if(rc!=1) $fatal(1,"PCM missing");
   if(j%7==3) begin pvalid=0;repeat(2) @(negedge clk);end
   pcm=sample;pvalid=1;plast=(j==length-1);
   @(posedge clk);while(!pready) @(posedge clk);
   @(negedge clk);pvalid=0;
  end
  plast=0;
  if(length>0) begin j=done_count; if(!done) begin wait(done_count>j);@(negedge clk);end end
  repeat(3) @(negedge clk);
 end
 repeat(20) @(negedge clk);
 rc=$fscanf(fout,"%d",exfft);if(rc==1) $fatal(1,"missing front outputs");
 $display("PASS FRONT transactions=%0d cycles=%0d",seen,cycles);$finish;
end
endmodule
'''

BACK_TB=r'''
`timescale 1ns/1ps
module tb_back;
logic clk=0,rst_n=0,valid=0,ready,last=0,err=0,outvalid,outready=0,outlast,outerror;
logic[59:0] mel=0;logic signed[7:0] s=0,outs;logic[31:0] frame=0,outframe;
logic[4:0] idx=0;logic[3:0] outidx;logic signed[39:0] mfcc;
integer cycles=0,seen=0,fin,fout,rc,j,n,ifrm,iidx,is,ilast,ierr;
reg[59:0] ivalue;reg signed[63:0] expected;
integer ef,ei,es,el,ee;
logic stalled=0;logic[87:0] held;
always #5 clk=~clk;
mfcc_fixed_log_dct U_DUT(.clk(clk),.rst_n(rst_n),.i_valid(valid),.o_ready(ready),.i_mel(mel),.i_bfp_s(s),.i_frame(frame),.i_index(idx),.i_last(last),.i_error(err),.o_valid(outvalid),.i_ready(outready),.o_mfcc(mfcc),.o_frame(outframe),.o_index(outidx),.o_bfp_s(outs),.o_last(outlast),.o_error(outerror));
always @(negedge clk) begin
 cycles=cycles+1;outready=(cycles%23>=8)&&!(cycles>=10000&&cycles<10500);
 if(cycles>10000000) $fatal(1,"back timeout");
end
always @(posedge clk) if(rst_n) begin
 if(stalled && {outvalid,mfcc,outframe,outidx,outs,outlast,outerror}!==held) $fatal(1,"back stall unstable");
 stalled=outvalid&&!outready;held={outvalid,mfcc,outframe,outidx,outs,outlast,outerror};
 if(outvalid&&outready) begin
  rc=$fscanf(fout,"%d %d %d %d %d %d\n",expected,ef,ei,es,el,ee);
  if(rc!=6) $fatal(1,"unexpected back output");
  if($signed(mfcc)!=expected || outframe!=ef || outidx!=ei || $signed(outs)!=es || outlast!=el || outerror!=ee)
    $fatal(1,"back mismatch n=%d got=%d/%d/%d/%d/%d/%d expected=%d/%d/%d/%d/%d/%d",seen,$signed(mfcc),outframe,outidx,$signed(outs),outlast,outerror,expected,ef,ei,es,el,ee);
  seen=seen+1;
 end
end
initial begin
 fin=$fopen("back_input.txt","r");fout=$fopen("back_expected.txt","r");
 if(!fin||!fout) $fatal(1,"files");
 repeat(4) @(negedge clk);rst_n=1;
 // Reset during an active nonfloor logarithm discards that transaction.
 valid=1;mel=60'd10000000000000;s=0;@(negedge clk);valid=0;
 repeat(10) @(negedge clk);rst_n=0;repeat(3) @(negedge clk);rst_n=1;
 rc=$fscanf(fin,"%d\n",n);
 for(j=0;j<n;j=j+1) begin
  rc=$fscanf(fin,"%d %d %d %d %d %d\n",ivalue,ifrm,iidx,is,ilast,ierr);
  if(rc!=6) $fatal(1,"missing back input");
  if(j%9==4) begin valid=0;repeat(3) @(negedge clk);end
  mel=ivalue;frame=ifrm;idx=iidx;s=is;last=ilast;err=ierr;valid=1;
  @(posedge clk);while(!ready) @(posedge clk);
  @(negedge clk);valid=0;
 end
 wait(seen==EXPECTED_COUNT);repeat(30) @(negedge clk);
 rc=$fscanf(fout,"%d",expected);if(rc==1) $fatal(1,"missing back outputs");
 $display("PASS BACK transactions=%0d cycles=%0d",seen,cycles);$finish;
end
endmodule
'''

LOG_TB=r'''
`timescale 1ns/1ps
module tb_log;
logic clk=0,rst_n=0,valid=0,ready,last=0,error=0,outvalid,outready=0,outlast,outfloor,outerror;
logic[59:0] mel=0;logic signed[5:0] s=0,outs;logic[31:0] frame=0,outframe;
logic[4:0] idx=0,outidx;logic signed[29:0] value;
integer cycles=0,seen=0,fin,fout,rc,j,n,is,ifr,ii,il,ie;
reg[59:0] iv;integer ev,ef,ei,es,el,eb,ee;
always #5 clk=~clk;
mfcc_fixed_log U_DUT(.clk(clk),.rst_n(rst_n),.i_valid(valid),.o_ready(ready),.i_mel(mel),.i_bfp_s(s),.i_frame(frame),.i_index(idx),.i_last(last),.i_error(error),.o_valid(outvalid),.i_ready(outready),.o_log(value),.o_frame(outframe),.o_index(outidx),.o_bfp_s(outs),.o_last(outlast),.o_floor(outfloor),.o_error(outerror));
always @(negedge clk) begin cycles=cycles+1;outready=cycles%17>5;if(cycles>1000000) $fatal(1,"log timeout");end
always @(posedge clk) if(rst_n&&outvalid&&outready) begin
 rc=$fscanf(fout,"%d %d %d %d %d %d %d\n",ev,ef,ei,es,el,eb,ee);
 if(rc!=7||$signed(value)!=ev||outframe!=ef||outidx!=ei||$signed(outs)!=es||outlast!=el||outfloor!=eb||outerror!=ee)
  $fatal(1,"log mismatch n=%d got=%d/%d/%d expected=%d/%d/%d",seen,$signed(value),outfloor,outerror,ev,eb,ee);
 seen=seen+1;
end
initial begin
 fin=$fopen("log_input.txt","r");fout=$fopen("log_expected.txt","r");
 if(!fin||!fout) $fatal(1,"log files");
 repeat(4) @(negedge clk);rst_n=1;
 rc=$fscanf(fin,"%d\n",n);
 for(j=0;j<n;j=j+1) begin
  rc=$fscanf(fin,"%d %d %d %d %d %d\n",iv,ifr,ii,is,il,ie);
  if(rc!=6) $fatal(1,"log input missing");
  mel=iv;frame=ifr;idx=ii;s=is;last=il;error=ie;valid=1;
  @(posedge clk);while(!ready) @(posedge clk);
  @(negedge clk);valid=0;
  if(j%9==3) repeat(3) @(negedge clk);
 end
 wait(seen==EXPECTED_COUNT);repeat(20) @(negedge clk);
 $display("PASS LOG transactions=%0d cycles=%0d",seen,cycles);$finish;
end
endmodule
'''

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--model-run',type=Path,required=True)
    ap.add_argument('--run-id',required=True)
    ap.add_argument('--synthesis',action='store_true')
    ap.add_argument('--units',default='log,front,back',help='comma-separated log,front,back subset')
    args=ap.parse_args()
    units=args.units.split(',')
    if not units or len(set(units))!=len(units) or any(u not in ('log','front','back') for u in units):
        ap.error('--units must be a unique subset of log,front,back')
    out=Path(r'D:\2610_MFCC\build\fixed_integer_rtl')/args.run_id
    out.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((args.model_run/'run_manifest.json').read_text())
    front_inputs=[str(len(manifest['cases']))];front_outputs=[];back_inputs=[];back_outputs=[];frame_id=0
    for case in manifest['cases']:
        pcm=np.fromfile(args.model_run/'arrays'/case['pcm']['file'],dtype='<i2')
        front_inputs += [str(len(pcm))]+[str(int(x)) for x in pcm]
        data=np.load(args.model_run/case['integer_file'])
        for f in range(case['frames']):
            s=int(data['shift_s'][f])
            for k,x in enumerate(data['fft_input'][f]):front_outputs.append(f'{x} {f} {k} {s} {int(k==511)} 0')
            for m,x in enumerate(data['mel_u60'][f]):back_inputs.append(f'{x} {frame_id} {m} {s} {int(m==25)} 0')
            for c,x in enumerate(data['mfcc_q24'][f]):back_outputs.append(f'{x} {frame_id} {c} {s} {int(c==12)} 0')
            frame_id+=1
    # Reuse valid numerical frame with deliberately bad metadata, then a clean frame.
    for error in (1,0):
        s=0
        logs=[log_integer(0,-43)[0]]*26
        from software.fixed_model.full_integer import dct_integer
        coefficients=json.loads((args.model_run/'coefficients.json').read_text())
        mfcc=dct_integer(logs,coefficients['dct_q30'])
        for m in range(26):back_inputs.append(f'0 {frame_id} {m if m!=7 or not error else 6} {s} {int(m==25)} 0')
        for c,x in enumerate(mfcc):back_outputs.append(f'{x} {frame_id} {c} 0 {int(c==12)} {error}')
        frame_id+=1
    log_inputs=[];log_outputs=[];rng=random.Random(20261004)
    for s in range(-2,25):
        threshold=(Fraction(1e-12)*(1<<(43+2*s))).__ceil__()
        values=[0,1,threshold-1,threshold,threshold+1,(1<<60)-1]+[rng.randrange(1<<60) for _ in range(32)]
        for value in values:
            ordinal=len(log_inputs);idx=ordinal%26;frame=ordinal//26
            result,floored=log_integer(value,-43-2*s)
            log_inputs.append(f'{value} {frame} {idx} {s} {int(idx==25)} 0')
            log_outputs.append(f'{result} {frame} {idx} {s} {int(idx==25)} {int(floored)} 0')
    for s in (-3,25,0):
        ordinal=len(log_inputs);idx=ordinal%26;frame=ordinal//26
        result,floored=(0,False) if s!=0 else log_integer(0,-43)
        log_inputs.append(f'0 {frame} {idx} {s} {int(idx==25)} 0')
        log_outputs.append(f'{result} {frame} {idx} {s} {int(idx==25)} {int(floored)} {int(s!=0)}')
    for name,lines in [('front_input.txt',front_inputs),('front_expected.txt',front_outputs),('back_input.txt',[str(len(back_inputs))]+back_inputs),('back_expected.txt',back_outputs),('log_input.txt',[str(len(log_inputs))]+log_inputs),('log_expected.txt',log_outputs)]:
        (out/name).write_text('\n'.join(lines)+'\n',encoding='ascii')
    front_tb=FRONT_TB
    back_tb=BACK_TB.replace('EXPECTED_COUNT',str(len(back_outputs)))
    log_tb=LOG_TB.replace('EXPECTED_COUNT',str(len(log_outputs)))
    for name in ('front_input.txt','front_expected.txt'):
        front_tb=front_tb.replace('"'+name+'"','"'+(out/name).as_posix()+'"')
    for name in ('back_input.txt','back_expected.txt'):
        back_tb=back_tb.replace('"'+name+'"','"'+(out/name).as_posix()+'"')
    for name in ('log_input.txt','log_expected.txt'):
        log_tb=log_tb.replace('"'+name+'"','"'+(out/name).as_posix()+'"')
    (out/'tb_front.sv').write_text(front_tb,encoding='utf-8')
    (out/'tb_back.sv').write_text(back_tb,encoding='utf-8')
    (out/'tb_log.sv').write_text(log_tb,encoding='utf-8')
    sources=list((ROOT/'hardware/fixed/frontend').glob('*.sv'))+list((ROOT/'hardware/fixed/log_dct').glob('*.sv'))
    snapshots=[]
    for source in sources:
        dest=out/'source'/source.relative_to(ROOT)
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,dest);snapshots.append(dest)
    tcl=[f'create_project fixed_integer_units {{{(out/"project").as_posix()}}} -part xc7z020clg400-1','set_property target_language Verilog [current_project]','set_property XPM_LIBRARIES {XPM_MEMORY} [current_project]']
    tcl += [f'read_verilog -sv {{{p.as_posix()}}}' for p in snapshots]
    tcl += [f'add_files -fileset sim_1 {{{(out/"tb_front.sv").as_posix()}}}',f'add_files -fileset sim_1 {{{(out/"tb_back.sv").as_posix()}}}',f'add_files -fileset sim_1 {{{(out/"tb_log.sv").as_posix()}}}','set_property xsim.simulate.runtime all [get_filesets sim_1]']
    for top in ['tb_'+unit for unit in units]:
        tcl += [f'set_property top {top} [get_filesets sim_1]','launch_simulation','close_sim']
    if args.synthesis:
        synth_tops=(['mfcc_fixed_frontend'] if 'front' in units else [])+(['mfcc_fixed_log_dct'] if any(u in units for u in ('log','back')) else [])
        for top in synth_tops:
            tcl += [f'synth_design -top {top} -part xc7z020clg400-1 -flatten_hierarchy rebuilt','create_clock -period 10.000 [get_ports clk]','set_input_delay 2.000 -clock clk [get_ports -filter {DIRECTION == IN && NAME != clk}]','set_output_delay 2.000 -clock clk [all_outputs]',f'report_utilization -file {top}_utilization.rpt',f'report_timing_summary -file {top}_timing.rpt',f'write_checkpoint -force {top}.dcp','close_design']
    tcl += ['exit']
    (out/'run.tcl').write_text('\n'.join(tcl)+'\n',encoding='utf-8')
    info={'units':units,'front_transactions':len(front_outputs),'back_transactions':len(back_outputs),'log_transactions':len(log_outputs),'corpus_frames':sum(case['frames'] for case in manifest['cases']),'back_frames_including_error_recovery':frame_id,'model_run':str(args.model_run),'sources':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},'synthesis_requested':args.synthesis}
    (out/'verification_manifest.json').write_text(json.dumps(info,indent=2),encoding='utf-8')
    print(out,flush=True)
    result=subprocess.run([r'C:\Xilinx\Vivado\2024.2\bin\vivado.bat','-mode','batch','-source','run.tcl','-log','vivado.log','-journal','vivado.jou'],cwd=out)
    text=(out/'vivado.log').read_text(encoding='utf-8',errors='replace') if (out/'vivado.log').exists() else ''
    marker_counts={'log':len(log_outputs),'front':len(front_outputs),'back':len(back_outputs)}
    required=[f'PASS {u.upper()} transactions={marker_counts[u]}' for u in units]
    passed=result.returncode==0 and all(marker in text for marker in required) and 'FATAL' not in text.upper()
    result_info={'status':'PASS' if passed else 'FAIL','status_scope':'integer transaction/protocol simulation and tool completion; timing acceptance is separate','accuracy_accepted':False,'timing_accepted':None,'timing_reports':[p.name for p in out.glob('*_timing.rpt')],'vivado_returncode':result.returncode,'required_markers':required,'markers_found':[marker for marker in required if marker in text]}
    (out/'verification_result.json').write_text(json.dumps(result_info,indent=2),encoding='utf-8')
    return 0 if passed else 1

if __name__=='__main__':raise SystemExit(main())
