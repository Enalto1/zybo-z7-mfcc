`timescale 1ns/1ps
module tb_fp32_frame_buffer;
    logic clk = 1'b0;
    logic rst_n = 1'b0;
    logic clip_start_valid = 1'b0;
    logic clip_start_ready;
    logic s_valid = 1'b0;
    logic s_ready;
    logic [31:0] s_data = 32'd0;
    logic clip_end_valid = 1'b0;
    logic clip_end_ready;
    logic m_valid;
    logic m_ready = 1'b0;
    logic [31:0] m_data;
    logic [8:0] m_sample_index;
    logic [31:0] m_frame_id;
    logic [31:0] m_start_sample;
    logic m_last;
    logic clip_done_valid;
    logic clip_done_ready = 1'b0;
    logic error;
    integer clip_tag = 0;
    integer accepted_samples = 0;
    integer received_words = 0;
    integer total_words = 0;
    integer completed_clips = 0;
    integer eof_during_output = 0;
    integer input_stall_cycles = 0;
    integer output_stall_cycles = 0;
    integer final_stall_cycles = 0;
    integer done_stall_cycles = 0;
    integer maximum_input = 0;
    integer input_gap_events = 0;
    integer write_wraps = 0, read_wraps = 0;
    logic [31:0] last_checked_frame = 0, last_checked_start = 0;
    logic ended = 1'b0;
    logic prev_stalled = 1'b0;
    logic [105:0] prev_payload;
    logic [31:0] lfsr = 32'h26101004;
    integer stall_left = 0;
    integer last_tag = -1;
    integer last_frame = -1;
    logic force_stall = 1'b0;
    logic random_stalls = 1'b1;

    fp32_frame_buffer #(.DATA_WIDTH(32)) DUT (
        .clk(clk), .rst_n(rst_n),
        .clip_start_valid(clip_start_valid), .clip_start_ready(clip_start_ready),
        .s_valid(s_valid), .s_ready(s_ready), .s_data(s_data),
        .clip_end_valid(clip_end_valid), .clip_end_ready(clip_end_ready),
        .m_valid(m_valid), .m_ready(m_ready), .m_data(m_data),
        .m_sample_index(m_sample_index), .m_frame_id(m_frame_id),
        .m_start_sample(m_start_sample), .m_last(m_last),
        .clip_done_valid(clip_done_valid), .clip_done_ready(clip_done_ready),
        .error(error)
    );

    always #5 clk = !clk;

    function automatic logic [31:0] pattern(input integer tag, input integer index);
        pattern = (32'h9e3779b9 * index) ^ (32'h7fc00001 + 32'h01010101 * tag);
    endfunction

    // Deterministic stalls, including a 97-cycle hold on every final beat.
    always @(negedge clk) begin
        lfsr = {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
        if (!rst_n) begin
            m_ready = 1'b0;
            stall_left = 0;
        end else if (force_stall) begin
            m_ready = 1'b0;
        end else if (stall_left > 0) begin
            m_ready = 1'b0;
            stall_left = stall_left - 1;
        end else if (m_valid && m_last && ((last_tag != clip_tag) || (last_frame != m_frame_id))) begin
            last_tag = clip_tag;
            last_frame = m_frame_id;
            stall_left = 96;
            m_ready = 1'b0;
        end else begin
            m_ready = !random_stalls || (lfsr[2:0] != 3'b000 && lfsr[2:0] != 3'b001);
        end
    end

    always @(posedge clk) begin : SCOREBOARD
        integer frame;
        integer index;
        integer expected_frames;
        if (!rst_n) begin
            accepted_samples = 0;
            received_words = 0;
            ended = 1'b0;
            prev_stalled = 1'b0;
        end else begin
            if (clip_start_valid && clip_start_ready) begin
                accepted_samples = 0;
                received_words = 0;
                ended = 1'b0;
                prev_stalled = 1'b0;
            end
            if (prev_stalled && (!m_valid || {m_data,m_sample_index,m_frame_id,m_start_sample,m_last} !== prev_payload))
                $fatal(1,"output changed while stalled");
            prev_stalled = m_valid && !m_ready;
            prev_payload = {m_data,m_sample_index,m_frame_id,m_start_sample,m_last};
            if (DUT.write_fire && DUT.read_enable) $fatal(1,"simultaneous RAM read/write");
            if (DUT.write_fire && DUT.write_addr_reg==9'd511) write_wraps++;
            if (DUT.read_enable && DUT.read_addr_reg==9'd511) read_wraps++;
            if (s_valid && !s_ready) input_stall_cycles++;
            if (m_valid && !m_ready) output_stall_cycles++;
            if (m_valid && m_last && !m_ready) final_stall_cycles++;
            if (clip_done_valid && !clip_done_ready) done_stall_cycles++;
            if (s_valid && s_ready) begin
                if (ended) $fatal(1,"input accepted after EOF");
                if (s_data !== pattern(clip_tag,accepted_samples)) $fatal(1,"TB input contract error");
                accepted_samples++;
                if (accepted_samples > maximum_input) maximum_input = accepted_samples;
            end
            if (clip_end_valid && clip_end_ready) begin
                if (ended) $fatal(1,"EOF accepted twice");
                if (m_valid || DUT.read_enable) eof_during_output++;
                ended = 1'b1;
            end
            if (m_valid && m_ready) begin
                frame = received_words / 512;
                index = received_words % 512;
                if (accepted_samples < frame*160+512) $fatal(1,"incomplete frame emitted");
                if (m_frame_id !== frame || m_start_sample !== frame*160 || m_sample_index !== index)
                    $fatal(1,"metadata mismatch tag=%0d word=%0d id=%0d start=%0d index=%0d",clip_tag,received_words,m_frame_id,m_start_sample,m_sample_index);
                if (m_data !== pattern(clip_tag,frame*160+index))
                    $fatal(1,"data mismatch tag=%0d frame=%0d index=%0d expected=%h actual=%h",clip_tag,frame,index,pattern(clip_tag,frame*160+index),m_data);
                if (m_last !== (index==511)) $fatal(1,"last mismatch");
                received_words++;
                total_words++;
                last_checked_frame=m_frame_id;
                last_checked_start=m_start_sample;
            end
            if (error) $fatal(1,"unexpected error flag");
            if (clip_done_valid) begin
                expected_frames = accepted_samples < 512 ? 0 : 1+(accepted_samples-512)/160;
                if (!ended || received_words != expected_frames*512 || m_valid)
                    $fatal(1,"done before correct frame drain: samples=%0d words=%0d",accepted_samples,received_words);
            end
            if (clip_done_valid && clip_done_ready) completed_clips++;
        end
    end

    task automatic start_clip(input integer tag);
        @(negedge clk);
        clip_tag = tag;
        clip_start_valid = 1'b1;
        do @(posedge clk); while (!clip_start_ready);
        @(negedge clk);
        clip_start_valid = 1'b0;
    endtask

    task automatic send_words(input integer count, input integer gap_mode);
        integer i;
        @(negedge clk);
        for (i=0;i<count;i++) begin
            if (gap_mode==1 && (i%13==0)) begin
                repeat ((i%5)+1) @(negedge clk);
            end else if (gap_mode==2 && lfsr[5:3]==3'b000) begin
                input_gap_events++;
                repeat (1+int'(lfsr[7:6])) @(negedge clk);
            end
            s_valid = 1'b1;
            s_data = pattern(clip_tag,i);
            do @(posedge clk); while (!s_ready);
            @(negedge clk);
            s_valid = 1'b0;
        end
    endtask

    task automatic finish_clip;
        @(negedge clk);
        clip_end_valid = 1'b1;
        do @(posedge clk); while (!clip_end_ready);
        @(negedge clk);
        clip_end_valid = 1'b0;
        wait (clip_done_valid);
        repeat (17) @(negedge clk);
        clip_done_ready = 1'b1;
        @(posedge clk);
        @(negedge clk);
        clip_done_ready = 1'b0;
        repeat (4) @(negedge clk);
        if (!clip_start_ready || m_valid || clip_done_valid) $fatal(1,"clip did not return idle");
    endtask

    task automatic run_clip(input integer tag,input integer count,input integer gaps);
        start_clip(tag);
        send_words(count,gaps);
        finish_clip();
        $display("F0_CASE_PASS tag=%0d samples=%0d frames=%0d",tag,count,count<512?0:1+(count-512)/160);
    endtask

    task automatic reset_dut;
        @(negedge clk);
        rst_n = 1'b0;
        s_valid = 1'b0;
        clip_start_valid = 1'b0;
        clip_end_valid = 1'b0;
        clip_done_ready = 1'b0;
        repeat(3) @(negedge clk);
        rst_n = 1'b1;
        repeat(2) @(negedge clk);
        if (!clip_start_ready || m_valid || clip_done_valid) $fatal(1,"reset state invalid");
    endtask

    initial begin
        reset_dut();
        if ($test$plusargs("F0_LONG_CLIP")) begin
            // One uninterrupted clip; payloads are injective 32-bit identities,
            // not audio/float math. No reset occurs between these534frames.
            run_clip(17,85920,2);
            if (completed_clips!=1 || accepted_samples!=85920 || received_words!=273408 ||
                total_words!=273408 || maximum_input!=85920 || last_checked_frame!==32'd533 ||
                last_checked_start!==32'd85280 || input_gap_events==0 || input_stall_cycles==0 ||
                output_stall_cycles==0 || final_stall_cycles<534*97 || done_stall_cycles<16 ||
                write_wraps!=167 || read_wraps!=534)
                $fatal(1,"F0 long-clip coverage/count failure");
            repeat(64) begin
                @(negedge clk);
                if(m_valid || clip_done_valid || !clip_start_ready || error)
                    $fatal(1,"F0 long-clip stale output after done");
            end
            $display("F0_LONG_PASS clips=1 accepted_words=%0d frames=534 compared_words=%0d last_frame=%0d last_start=%0d tail_discarded=128 write_wraps=%0d read_wraps=%0d input_gap_events=%0d input_stalls=%0d output_stalls=%0d final_stalls=%0d done_stalls=%0d",
                accepted_samples,total_words,last_checked_frame,last_checked_start,write_wraps,read_wraps,input_gap_events,input_stall_cycles,output_stall_cycles,final_stall_cycles,done_stall_cycles);
            $finish;
        end
        run_clip(1,0,0);
        run_clip(2,1,1);
        run_clip(3,511,1);
        run_clip(4,512,0);
        run_clip(5,671,1);
        run_clip(6,672,0);
        run_clip(7,831,1);
        run_clip(8,832,0);
        run_clip(9,4512,1);
        // Abort during collection, then prove new-clipped RAM history is isolated.
        start_clip(10); send_words(511,0); reset_dut();
        run_clip(11,672,1);
        // Abort an issued read before the wrapper captures it.
        start_clip(12); send_words(512,0);
        reset_dut();
        run_clip(13,512,1);
        // Abort a stalled middle output beat, then start another independent clip.
        start_clip(14); send_words(512,0);
        wait (m_valid && m_sample_index==9'd17);
        @(negedge clk); force_stall=1'b1;
        repeat(23) @(negedge clk);
        reset_dut();
        @(negedge clk); force_stall=1'b0;
        run_clip(15,832,1);
        // EOF arrives while the final output beat is held under backpressure.
        start_clip(16); send_words(512,0);
        wait (m_valid && m_last);
        finish_clip();
        $display("F0_CASE_PASS tag=16 samples=512 frames=1 eof_during_last_stall=1");
        if (completed_clips!=13 || input_stall_cycles==0 || final_stall_cycles<100 || done_stall_cycles<100 || maximum_input!=4512 || eof_during_output==0)
            $fatal(1,"coverage missing clips=%0d input_stalls=%0d last_stalls=%0d",completed_clips,input_stall_cycles,final_stall_cycles);
        $display("F0_PASS completed_clips=%0d compared_words=%0d input_stalls=%0d output_stalls=%0d final_stalls=%0d done_stalls=%0d eof_during_output=%0d max_input=%0d",completed_clips,total_words,input_stall_cycles,output_stall_cycles,final_stall_cycles,done_stall_cycles,eof_during_output,maximum_input);
        $finish;
    end

    initial begin
        if ($test$plusargs("F0_LONG_CLIP")) #100000000;
        else #10000000;
        $fatal(1,"watchdog timeout");
    end
endmodule
