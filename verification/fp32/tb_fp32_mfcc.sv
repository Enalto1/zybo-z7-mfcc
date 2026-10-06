`timescale 1ns/1ps
// Verification-only file: procedural loops/tasks/file I/O do not enter synthesis.
module tb_fp32_mfcc;
    logic clk=0, rst_n=0;
    always #5 clk=~clk;
    logic clip_start_valid=0, clip_start_ready;
    logic s_valid=0, s_ready;
    logic [15:0] s_pcm=0;
    logic clip_end_valid=0, clip_end_ready;
    logic m_valid, m_ready=0;
    logic [31:0] m_data, m_frame_id, m_start_sample;
    logic [3:0] m_coeff_index;
    logic m_last, clip_done_valid, clip_done_ready=0, o_busy;
    logic [11:0] o_error;
    fp32_mfcc U_DUT (
        .clk(clk), .rst_n(rst_n), .clip_start_valid(clip_start_valid), .clip_start_ready(clip_start_ready),
        .s_valid(s_valid), .s_ready(s_ready), .s_pcm(s_pcm), .clip_end_valid(clip_end_valid), .clip_end_ready(clip_end_ready),
        .m_valid(m_valid), .m_ready(m_ready), .m_data(m_data), .m_coeff_index(m_coeff_index),
        .m_frame_id(m_frame_id), .m_start_sample(m_start_sample), .m_last(m_last),
        .clip_done_valid(clip_done_valid), .clip_done_ready(clip_done_ready), .o_error(o_error), .o_busy(o_busy)
    );
    integer cases_fd, input_fd, trace_fd, summary_fd, detail_fd, status;
    integer case_id=-1, sample_count, expected_frames, sent_count, out_count;
    integer input_count, pre_count, frame_count, window_count, completed=0;
    integer stall_cycles=0, last_stalled_frame=-1;
    longint cycle=0, case_start=0;
    logic [31:0] random_state=32'h20261004;
    logic stalled=0;
    logic [100:0] held;
    // Passive protocol accounting; these counters never drive the DUT or PRNG.
    logic case_tracking=0, clip_active=0, eof_seen=0, idle_watch=0;
    logic input_stalled=0, pre_stalled=0, frame_stalled=0, window_stalled=0;
    logic [15:0] held_pcm;
    logic [31:0] held_pre;
    logic [105:0] held_frame, held_window;
    logic previous_rst_n=0, initial_reset_released=0;
    integer reset_assertions=1, initial_reset_low_cycles=0, case_reset_baseline;
    integer start_accepts, end_accepts, done_accepts, pcm_accepts;
    integer input_gap_events, input_gap_cycles, input_stall_cycles;
    integer pre_stall_cycles, frame_stall_cycles, window_stall_cycles;
    integer output_stall_cycles, output_last_stall_cycles, forced_last_stalls;
    integer done_hold_cycles, post_done_idle_cycles, expected_tail;
    logic [31:0] final_frame_id, final_start_sample;
    logic [3:0] final_coeff_index;
    longint start_accept_cycle, last_pcm_accept_cycle, eof_accept_cycle;
    longint last_c12_accept_cycle, first_done_valid_cycle, done_accept_cycle;
    longint case_protocol_cycles;
    string cases_path, out_path, pcm_path;
    logic [15:0] pcm_word;
    `include "reset/reset_stress_tasks.svh"

    always @(negedge clk) begin
        if (rst_n) begin
            random_state = {random_state[30:0],random_state[31]^random_state[21]^random_state[1]^random_state[0]};
            if (stall_cycles > 0) begin
                m_ready=0; stall_cycles=stall_cycles-1;
            end else if (m_valid && m_last && last_stalled_frame != m_frame_id) begin
                m_ready=0; stall_cycles=97; last_stalled_frame=m_frame_id;
                if (case_tracking) forced_last_stalls=forced_last_stalls+1;
            end else m_ready = (random_state[2:0] != 3'b000);
        end else m_ready=0;
    end
    always @(posedge clk) begin
        cycle=cycle+1;
        if (!initial_reset_released && !rst_n) initial_reset_low_cycles=initial_reset_low_cycles+1;
        if (rst_n) initial_reset_released=1;
        if (previous_rst_n && !rst_n) reset_assertions=reset_assertions+1;
        previous_rst_n=rst_n;
        if (case_tracking && rst_n !== 1'b1) $fatal(1,"RESET_DURING_NORMAL_CLIP");
        if (rst_n && case_tracking) begin
            if (o_error !== 12'd0) $fatal(1,"DUT_ERROR case=%0d cycle=%0d flags=%h",case_id,cycle,o_error);
            if ($isunknown({s_valid,s_ready,clip_start_valid,clip_start_ready,clip_end_valid,clip_end_ready,
                            clip_done_valid,clip_done_ready,m_valid,m_ready,o_busy,
                            U_DUT.pre_valid,U_DUT.pre_ready,U_DUT.fr_valid,U_DUT.fr_ready,
                            U_DUT.win_valid,U_DUT.win_ready})) $fatal(1,"UNKNOWN_PROTOCOL_CONTROL");
            // Sample the old state before accepting start/done on this edge (NBA-safe).
            if (clip_active && o_busy !== 1'b1) $fatal(1,"BUSY_DROPPED_BEFORE_DONE_ACCEPT");
            if (!clip_active && o_busy !== 1'b0) $fatal(1,"BUSY_OUTSIDE_CLIP");
            if (input_stalled && (!s_valid || s_pcm !== held_pcm)) $fatal(1,"INPUT_CHANGED_WHILE_STALLED");
            if (pre_stalled && (!U_DUT.pre_valid || U_DUT.pre_data !== held_pre)) $fatal(1,"PRE_CHANGED_WHILE_STALLED");
            if (frame_stalled && (!U_DUT.fr_valid ||
                {U_DUT.fr_data,U_DUT.fr_index,U_DUT.fr_frame,U_DUT.fr_start,U_DUT.fr_last} !== held_frame))
                $fatal(1,"FRAME_CHANGED_WHILE_STALLED");
            if (window_stalled && (!U_DUT.win_valid ||
                {U_DUT.win_data,U_DUT.win_index,U_DUT.win_frame,U_DUT.win_start,U_DUT.win_last} !== held_window))
                $fatal(1,"WINDOW_CHANGED_WHILE_STALLED");
            input_stalled=s_valid && !s_ready; held_pcm=s_pcm;
            pre_stalled=U_DUT.pre_valid && !U_DUT.pre_ready; held_pre=U_DUT.pre_data;
            frame_stalled=U_DUT.fr_valid && !U_DUT.fr_ready;
            held_frame={U_DUT.fr_data,U_DUT.fr_index,U_DUT.fr_frame,U_DUT.fr_start,U_DUT.fr_last};
            window_stalled=U_DUT.win_valid && !U_DUT.win_ready;
            held_window={U_DUT.win_data,U_DUT.win_index,U_DUT.win_frame,U_DUT.win_start,U_DUT.win_last};
            if (input_stalled) input_stall_cycles=input_stall_cycles+1;
            if (pre_stalled) pre_stall_cycles=pre_stall_cycles+1;
            if (frame_stalled) frame_stall_cycles=frame_stall_cycles+1;
            if (window_stalled) window_stall_cycles=window_stall_cycles+1;
            if (m_valid && !m_ready) output_stall_cycles=output_stall_cycles+1;
            if (m_valid && m_last && !m_ready) output_last_stall_cycles=output_last_stall_cycles+1;
            if (clip_active && !eof_seen && pcm_accepts<sample_count && !s_valid)
                input_gap_cycles=input_gap_cycles+1;
            if (clip_start_valid && clip_start_ready) begin
                if (clip_active || start_accepts!=0) $fatal(1,"DUPLICATE_CLIP_START");
                start_accepts=start_accepts+1; start_accept_cycle=cycle; clip_active=1;
            end
            if (s_valid && s_ready) begin
                if (!clip_active || eof_seen || pcm_accepts>=sample_count) $fatal(1,"PCM_ACCEPT_OUTSIDE_PAYLOAD");
                if ($isunknown(s_pcm)) $fatal(1,"UNKNOWN_PCM");
                pcm_accepts=pcm_accepts+1; last_pcm_accept_cycle=cycle;
            end
            if (clip_end_valid && clip_end_ready) begin
                if (!clip_active || eof_seen || pcm_accepts!=sample_count) $fatal(1,"BAD_EOF_ACCEPT");
                end_accepts=end_accepts+1; eof_accept_cycle=cycle; eof_seen=1;
            end
            if (clip_done_valid) begin
                if (!clip_active || !eof_seen || end_accepts!=1 || pcm_accepts!=sample_count ||
                    input_count!=sample_count || pre_count!=sample_count ||
                    frame_count!=expected_frames*512 || window_count!=expected_frames*512 ||
                    out_count!=expected_frames*13 || m_valid || U_DUT.pre_valid || U_DUT.fr_valid || U_DUT.win_valid)
                    $fatal(1,"PREMATURE_OR_UNDRAINED_DONE");
                if (first_done_valid_cycle<0) first_done_valid_cycle=cycle;
                if (!clip_done_ready) done_hold_cycles=done_hold_cycles+1;
            end
            if (clip_done_valid && clip_done_ready) begin
                if (done_accepts!=0) $fatal(1,"DUPLICATE_DONE_ACCEPT");
                done_accepts=done_accepts+1; done_accept_cycle=cycle; clip_active=0;
            end
            if (idle_watch) begin
                if (o_busy || !clip_start_ready || s_ready || clip_end_ready || clip_done_valid ||
                    m_valid || U_DUT.input_dbg_valid || U_DUT.pre_valid || U_DUT.fr_valid ||
                    U_DUT.win_valid || U_DUT.dbg_valid) $fatal(1,"POST_DONE_NOT_IDLE");
                post_done_idle_cycles=post_done_idle_cycles+1;
            end
            if (cycle-case_start > 2000000 + expected_frames*400000 + sample_count*200)
                $fatal(1,"TIMEOUT case=%0d output_count=%0d",case_id,out_count);
            if (stalled && (!m_valid || {m_data,m_coeff_index,m_frame_id,m_start_sample,m_last} !== held))
                $fatal(1,"OUTPUT_CHANGED_WHILE_STALLED");
            stalled=m_valid && !m_ready;
            held={m_data,m_coeff_index,m_frame_id,m_start_sample,m_last};
            if (U_DUT.input_dbg_valid) begin
                $fwrite(trace_fd,"%0d 0 0 %0d %08h 00000000 %0d\n",case_id,input_count,U_DUT.input_dbg_data,cycle);
                input_count=input_count+1;
            end
            if (U_DUT.pre_valid && U_DUT.pre_ready) begin
                $fwrite(trace_fd,"%0d 10 0 %0d %08h 00000000 %0d\n",case_id,pre_count,U_DUT.pre_data,cycle);
                pre_count=pre_count+1;
            end
            if (U_DUT.fr_valid && U_DUT.fr_ready) begin
                if (U_DUT.fr_frame !== frame_count/512 || U_DUT.fr_index !== frame_count%512 ||
                    U_DUT.fr_start !== (frame_count/512)*160 || U_DUT.fr_last !== (frame_count%512==511))
                    $fatal(1,"FRAME_METADATA");
                $fwrite(trace_fd,"%0d 11 %0d %0d %08h 00000000 %0d\n",case_id,U_DUT.fr_frame,U_DUT.fr_index,U_DUT.fr_data,cycle);
                frame_count=frame_count+1;
            end
            if (U_DUT.win_valid && U_DUT.win_ready) begin
                if (U_DUT.win_frame !== window_count/512 || U_DUT.win_index !== window_count%512 ||
                    U_DUT.win_start !== (window_count/512)*160 || U_DUT.win_last !== (window_count%512==511))
                    $fatal(1,"WINDOW_METADATA");
                $fwrite(trace_fd,"%0d 12 %0d %0d %08h 00000000 %0d\n",case_id,U_DUT.win_frame,U_DUT.win_index,U_DUT.win_data,cycle);
                window_count=window_count+1;
            end
            if (U_DUT.dbg_valid) begin
                $fwrite(trace_fd,"%0d %0d %0d %0d %08h %08h %0d\n",case_id,U_DUT.dbg_stage,
                    m_frame_id,U_DUT.dbg_index,U_DUT.dbg_data,U_DUT.dbg_aux,cycle);
            end
            if (m_valid && m_ready) begin
                if (m_frame_id !== out_count/13 || m_coeff_index !== out_count%13 ||
                    m_start_sample !== (out_count/13)*160 || m_last !== (out_count%13==12))
                    $fatal(1,"MFCC_METADATA case=%0d count=%0d frame=%0d coeff=%0d",case_id,out_count,m_frame_id,m_coeff_index);
                if (m_data[30:23] == 8'hff || $isunknown(m_data)) $fatal(1,"NONFINITE_MFCC");
                $fwrite(trace_fd,"%0d 6 %0d %0d %08h 00000000 %0d\n",case_id,m_frame_id,m_coeff_index,m_data,cycle);
                final_frame_id=m_frame_id; final_start_sample=m_start_sample; final_coeff_index=m_coeff_index;
                if (m_last) last_c12_accept_cycle=cycle;
                out_count=out_count+1;
            end
        end
    end

    initial begin
        if (!$value$plusargs("CASES=%s",cases_path)) cases_path="cases.txt";
        if (!$value$plusargs("OUTPUT=%s",out_path)) out_path=".";
        cases_fd=$fopen(cases_path,"r");
        trace_fd=$fopen({out_path,"/traces.txt"},"w");
        summary_fd=$fopen({out_path,"/simulation_summary.txt"},"w");
        detail_fd=$fopen({out_path,"/protocol_detail.txt"},"w");
        if (!cases_fd || !trace_fd || !summary_fd || !detail_fd) $fatal(1,"OPEN_FAILED");
        repeat(16) @(negedge clk);
        rst_n=1;
        repeat(16) @(negedge clk);
        if ($test$plusargs("RESET_STRESS")) run_reset_stress();
        while (!$feof(cases_fd)) begin
            status=$fscanf(cases_fd,"%s %d %d\n",pcm_path,sample_count,case_id);
            if (status != 3) $fatal(1,"BAD_CASES_FILE");
            expected_frames=(sample_count<512)?0:(1+(sample_count-512)/160);
            expected_tail=(expected_frames==0)?sample_count:(sample_count-((expected_frames-1)*160+512));
            sent_count=0; out_count=0; input_count=0; pre_count=0; frame_count=0; window_count=0;
            last_stalled_frame=-1; case_start=cycle; stalled=0;
            start_accepts=0; end_accepts=0; done_accepts=0; pcm_accepts=0;
            input_gap_events=0; input_gap_cycles=0; input_stall_cycles=0;
            pre_stall_cycles=0; frame_stall_cycles=0; window_stall_cycles=0;
            output_stall_cycles=0; output_last_stall_cycles=0; forced_last_stalls=0;
            done_hold_cycles=0; post_done_idle_cycles=0;
            start_accept_cycle=-1; last_pcm_accept_cycle=-1; eof_accept_cycle=-1;
            last_c12_accept_cycle=-1; first_done_valid_cycle=-1; done_accept_cycle=-1;
            final_frame_id=32'hffffffff; final_start_sample=32'hffffffff; final_coeff_index=4'hf;
            input_stalled=0; pre_stalled=0; frame_stalled=0; window_stalled=0;
            clip_active=0; eof_seen=0; idle_watch=0; case_reset_baseline=reset_assertions; case_tracking=1;
            @(negedge clk); clip_start_valid=1;
            do @(posedge clk); while (!clip_start_ready);
            @(negedge clk); clip_start_valid=0;
            input_fd=$fopen(pcm_path,"r");
            if (!input_fd) $fatal(1,"PCM_OPEN_FAILED");
            repeat(sample_count) begin
                status=$fscanf(input_fd,"%h\n",pcm_word);
                if (status != 1) $fatal(1,"PCM_SHORT");
                if (random_state[4:3]==0) begin
                    input_gap_events=input_gap_events+1;
                    repeat(3) @(negedge clk);
                end
                s_pcm=pcm_word; s_valid=1;
                do @(posedge clk); while (!s_ready);
                sent_count=sent_count+1;
                @(negedge clk); s_valid=0;
            end
            $fclose(input_fd);
            clip_end_valid=1;
            do @(posedge clk); while (!clip_end_ready);
            @(negedge clk); clip_end_valid=0;
            do @(posedge clk); while (!clip_done_valid);
            @(negedge clk);
            if (sent_count != sample_count || input_count != sample_count || pre_count != sample_count ||
                frame_count != expected_frames*512 || window_count != expected_frames*512 || out_count != expected_frames*13)
                $fatal(1,"COUNT_MISMATCH case=%0d sent=%0d in=%0d pre=%0d frames=%0d window=%0d out=%0d",case_id,sent_count,input_count,pre_count,frame_count,window_count,out_count);
            repeat(13) begin @(negedge clk); if (!clip_done_valid || m_valid) $fatal(1,"DONE_NOT_HELD"); end
            case_protocol_cycles=cycle-case_start;
            clip_done_ready=1;
            @(negedge clk); clip_done_ready=0;
            idle_watch=1;
            repeat(64) @(negedge clk);
            idle_watch=0;
            if (start_accepts!=1 || end_accepts!=1 || done_accepts!=1 || pcm_accepts!=sample_count ||
                reset_assertions!=case_reset_baseline || done_hold_cycles<13 || post_done_idle_cycles!=64 ||
                out_count!=expected_frames*13 || frame_count!=expected_frames*512 || window_count!=expected_frames*512)
                $fatal(1,"LIFECYCLE_COVERAGE_MISMATCH");
            if (!$test$plusargs("RESET_STRESS") && (reset_assertions!=1 || initial_reset_low_cycles!=16))
                $fatal(1,"EXPECTED_ONE_INITIAL_16_CLOCK_RESET");
            if (expected_frames>0 && (final_frame_id!==(expected_frames-1) ||
                final_start_sample!==((expected_frames-1)*160) || final_coeff_index!==4'd12 ||
                forced_last_stalls!=expected_frames || output_stall_cycles==0 || output_last_stall_cycles==0))
                $fatal(1,"FINAL_FRAME_OR_STALL_COVERAGE_MISMATCH");
            if (sample_count==85920 && (expected_frames!=534 || expected_tail!=128 || out_count!=6942 ||
                final_frame_id!==32'd533 || final_start_sample!==32'd85280 ||
                input_gap_events==0 || input_gap_cycles==0 || input_stall_cycles==0))
                $fatal(1,"CONTINUOUS_85920_COVERAGE_MISMATCH");
            $fwrite(detail_fd,"PROTOCOL_DETAIL case=%0d samples=%0d frames=%0d coeffs=%0d pcm_accepts=%0d input_converted=%0d pre_accepts=%0d frame_words=%0d window_words=%0d tail_discarded=%0d final_frame=%0d final_start=%0d final_coeff=%0d\n",case_id,sample_count,expected_frames,out_count,pcm_accepts,input_count,pre_count,frame_count,window_count,expected_tail,final_frame_id,final_start_sample,final_coeff_index);
            $fwrite(detail_fd,"LIFECYCLE case=%0d start_accepts=%0d eof_accepts=%0d done_accepts=%0d total_reset_assertions=%0d initial_reset_low_clocks=%0d resets_during_case=%0d done_hold_clocks=%0d post_done_idle_clocks=%0d\n",case_id,start_accepts,end_accepts,done_accepts,reset_assertions,initial_reset_low_cycles,reset_assertions-case_reset_baseline,done_hold_cycles,post_done_idle_cycles);
            $fwrite(detail_fd,"COVERAGE case=%0d stimulus_gap_events=%0d input_gap_clocks=%0d input_stall_clocks=%0d pre_stall_clocks=%0d frame_stall_clocks=%0d window_stall_clocks=%0d output_stall_clocks=%0d output_last_stall_clocks=%0d forced_last_stall_events=%0d\n",case_id,input_gap_events,input_gap_cycles,input_stall_cycles,pre_stall_cycles,frame_stall_cycles,window_stall_cycles,output_stall_cycles,output_last_stall_cycles,forced_last_stalls);
            $fwrite(detail_fd,"CYCLES case=%0d clip_start_accept=%0d last_pcm_accept=%0d eof_accept=%0d last_c12_accept=%0d first_done_valid=%0d done_accept=%0d protocol_elapsed=%0d post_idle_end=%0d\n",case_id,start_accept_cycle,last_pcm_accept_cycle,eof_accept_cycle,last_c12_accept_cycle,first_done_valid_cycle,done_accept_cycle,case_protocol_cycles,cycle);
            $fwrite(summary_fd,"PASS %0d samples=%0d frames=%0d coeffs=%0d cycles=%0d\n",case_id,sample_count,expected_frames,out_count,case_protocol_cycles);
            $display("CASE_PASS %0d samples=%0d frames=%0d cycles=%0d",case_id,sample_count,expected_frames,case_protocol_cycles);
            case_tracking=0;
            completed=completed+1;
        end
        $fwrite(summary_fd,"ALL_PROTOCOL_PASS cases=%0d\n",completed);
        $fwrite(detail_fd,"ALL_PROTOCOL_DETAIL_PASS cases=%0d\n",completed);
        $fclose(trace_fd); $fclose(summary_fd); $fclose(detail_fd); $fclose(cases_fd);
        $display("ALL_PROTOCOL_PASS cases=%0d",completed);
        $finish;
    end
endmodule
