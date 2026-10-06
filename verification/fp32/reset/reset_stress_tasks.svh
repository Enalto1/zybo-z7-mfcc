// Testbench-only include. No force/deposit and no numerical behavioral replacement.
logic [15:0] rs_pcm[0:511];
logic [31:0] rs_golden[0:12];
integer rs_summary_fd, rs_output_fd;
integer rs_phase = -1;
longint rs_started;
string rs_pcm_path;

task automatic rs_assert_clean;
    if (o_error !== 12'd0) $fatal(1,"RESET_STRESS_ERROR phase=%0d flags=%h",rs_phase,o_error);
endtask

task automatic rs_reset;
    @(negedge clk);
    rst_n=0;
    s_valid=0; clip_start_valid=0; clip_end_valid=0; clip_done_ready=0;
    stalled=0; stall_cycles=0; last_stalled_frame=-1;
    repeat(16) begin
        @(negedge clk);
        if (m_valid || clip_done_valid || o_busy || o_error!==12'd0)
            $fatal(1,"RESET_STRESS_NOT_CLEARED phase=%0d",rs_phase);
    end
    rst_n=1;
    repeat(16) begin
        @(negedge clk);
        rs_assert_clean();
        if(m_valid || clip_done_valid || o_busy) $fatal(1,"RESET_STRESS_STALE_OUTPUT phase=%0d",rs_phase);
    end
    if(!clip_start_ready) $fatal(1,"RESET_STRESS_NO_RESTART_READY");
endtask

task automatic rs_begin_clip;
    @(negedge clk);
    last_stalled_frame=-1; stall_cycles=0;
    clip_start_valid=1;
    do @(posedge clk); while(!clip_start_ready);
    @(negedge clk);clip_start_valid=0;
endtask

task automatic rs_send(input integer count);
    integer item;
    for(item=0;item<count;item++)begin
        s_pcm=rs_pcm[item];s_valid=1;
        do begin @(posedge clk);rs_assert_clean();end while(!s_ready);
        @(negedge clk);s_valid=0;
    end
endtask

task automatic rs_good_clip(input integer trial,input bit record_golden);
    integer coefficient;
    logic held_valid;
    logic [100:0] held_value;
    rs_begin_clip();
    rs_send(512);
    clip_end_valid=1;
    do begin @(posedge clk);rs_assert_clean();end while(!clip_end_ready);
    @(negedge clk);clip_end_valid=0;
    coefficient=0;held_valid=0;
    while(coefficient<13)begin
        @(posedge clk);rs_assert_clean();
        if(held_valid && (!m_valid || {m_data,m_coeff_index,m_frame_id,m_start_sample,m_last}!==held_value))
            $fatal(1,"RESET_STRESS_STALL_MUTATION trial=%0d",trial);
        held_valid=m_valid&&!m_ready;
        held_value={m_data,m_coeff_index,m_frame_id,m_start_sample,m_last};
        if(clip_done_valid) $fatal(1,"RESET_STRESS_PREMATURE_DONE trial=%0d",trial);
        if(m_valid&&m_ready)begin
            if(m_frame_id!==32'd0 || m_start_sample!==32'd0 || m_coeff_index!==coefficient || m_last!==(coefficient==12))
                $fatal(1,"RESET_STRESS_METADATA trial=%0d coeff=%0d",trial,coefficient);
            if($isunknown(m_data) || m_data[30:23]==8'hff) $fatal(1,"RESET_STRESS_NONFINITE");
            if(record_golden)rs_golden[coefficient]=m_data;
            else if(m_data!==rs_golden[coefficient])
                $fatal(1,"RESET_STRESS_BIT_MISMATCH trial=%0d coeff=%0d expected=%h actual=%h",trial,coefficient,rs_golden[coefficient],m_data);
            $fwrite(rs_output_fd,"%0d %0d %08h\n",trial,coefficient,m_data);
            coefficient++;
        end
    end
    do begin
        @(posedge clk);rs_assert_clean();
        if(m_valid) $fatal(1,"RESET_STRESS_EXTRA_OUTPUT trial=%0d",trial);
    end while(!clip_done_valid);
    repeat(13)begin
        @(negedge clk);rs_assert_clean();
        if(!clip_done_valid || m_valid) $fatal(1,"RESET_STRESS_DONE_NOT_HELD trial=%0d",trial);
    end
    clip_done_ready=1;
    @(posedge clk);
    @(negedge clk);clip_done_ready=0;
    repeat(4)@(negedge clk);
    if(!clip_start_ready || m_valid || clip_done_valid || o_busy) $fatal(1,"RESET_STRESS_NOT_IDLE trial=%0d",trial);
    $fwrite(rs_summary_fd,"RESET_REPLAY_PASS trial=%0d coeffs=13 bit_equal=%0d\n",trial,!record_golden);
    $display("RESET_REPLAY_PASS trial=%0d coeffs=13 bit_equal=%0d",trial,!record_golden);
endtask

task automatic rs_wait_abort_point(input integer phase);
    bit reached;
    reached=0;
    while(!reached)begin
        @(posedge clk);rs_assert_clean();
        case(phase)
            0: reached=U_DUT.U_PREEMPHASIS.conv_s_valid && U_DUT.U_PREEMPHASIS.conv_s_ready;
            1: reached=U_DUT.U_FRAME_BUFFER.read_enable && U_DUT.fr_index==9'd17;
            2: reached=U_DUT.U_BACKEND.fft_input_valid && U_DUT.U_BACKEND.fft_input_ready && U_DUT.U_BACKEND.fft_input_last;
            3: reached=U_DUT.U_BACKEND.U_ALU.log_a_valid && U_DUT.U_BACKEND.U_ALU.log_a_ready;
            4: reached=m_valid && m_last && !m_ready;
            default:$fatal(1,"RESET_STRESS_BAD_PHASE");
        endcase
    end
    $fwrite(rs_summary_fd,"RESET_ABORT_POINT phase=%0d cycle=%0d\n",phase,cycle);
    $display("RESET_ABORT_POINT phase=%0d cycle=%0d",phase,cycle);
endtask

task automatic run_reset_stress;
    integer pcm_fd,item,read_status;
    if(!$value$plusargs("RESET_PCM=%s",rs_pcm_path))$fatal(1,"RESET_STRESS_REQUIRES_RESET_PCM");
    pcm_fd=$fopen(rs_pcm_path,"r");
    rs_summary_fd=$fopen({out_path,"/reset_summary.txt"},"w");
    rs_output_fd=$fopen({out_path,"/reset_outputs.txt"},"w");
    if(!pcm_fd || !rs_summary_fd || !rs_output_fd)$fatal(1,"RESET_STRESS_OPEN_FAILED");
    for(item=0;item<512;item++)begin
        read_status=$fscanf(pcm_fd,"%h\n",rs_pcm[item]);
        if(read_status!=1)$fatal(1,"RESET_STRESS_PCM_SHORT");
    end
    $fclose(pcm_fd);
    // case_id remains -1: abort trials never contaminate normal numerical traces.
    case_id=-1;rs_started=cycle;
    fork
        begin
            repeat(2000000)@(posedge clk);
            $fatal(1,"RESET_STRESS_WATCHDOG phase=%0d",rs_phase);
        end
        begin
            rs_reset();
            rs_good_clip(0,1'b1);
            for(rs_phase=0;rs_phase<5;rs_phase++)begin
                rs_begin_clip();
                rs_send(rs_phase==0?1:512);
                rs_wait_abort_point(rs_phase);
                rs_reset();
                rs_good_clip(rs_phase+1,1'b0);
            end
        end
    join_any
    disable fork;
    $fwrite(rs_summary_fd,"RESET_STRESS_PASS phases=5 baseline_frames=1 replay_frames=5 reset_low_clocks=16 cycles=%0d\n",cycle-rs_started);
    $display("RESET_STRESS_PASS phases=5 baseline_frames=1 replay_frames=5 reset_low_clocks=16 cycles=%0d",cycle-rs_started);
    $fclose(rs_output_fd);$fclose(rs_summary_fd);
endtask
