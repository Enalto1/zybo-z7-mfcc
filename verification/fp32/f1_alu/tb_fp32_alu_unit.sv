`timescale 1ns/1ps
// Verification only. Actual generated vendor arithmetic remains instantiated.
module tb_fp32_alu_unit;
    logic clk = 0, rst_n = 0;
    always #5 clk = ~clk;
    logic req_valid = 0, req_ready;
    logic [1:0] req_op = 0;
    logic [31:0] req_a = 0, req_b = 0;
    logic rsp_valid, rsp_ready = 0, o_error;
    logic [31:0] rsp_data;
    integer output_fd, checks_fd;
    integer request_count = 0, response_count = 0, completed = 0;
    integer asymmetric_accept_cycles = 0;
    integer mul_paused_cycles = 0, add_paused_cycles = 0, log_paused_cycles = 0;
    longint cycles = 0;
    logic hold_active = 0;
    logic [31:0] held_data = 0;
    string output_path;

    fp32_alu #(.INCLUDE_LOG(1'b1)) U_DUT (
        .clk(clk), .rst_n(rst_n), .req_valid(req_valid), .req_ready(req_ready),
        .req_op(req_op), .req_a(req_a), .req_b(req_b),
        .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_data(rsp_data), .o_error(o_error)
    );

    always @(posedge clk) begin
        cycles = cycles + 1;
        if (cycles > 20000) $fatal(1, "ALU_TIMEOUT");
        if (rst_n) begin
            if (o_error !== 1'b0) $fatal(1, "ALU_ERROR");
            if (req_valid && req_ready) request_count = request_count + 1;
            if (rsp_valid && rsp_ready) response_count = response_count + 1;
            if (hold_active && (!rsp_valid || rsp_data !== held_data))
                $fatal(1, "ALU_RESPONSE_NOT_HELD");
            hold_active = rsp_valid && !rsp_ready;
            held_data = rsp_data;
            if ((U_DUT.mul_a_valid && U_DUT.mul_a_ready) != (U_DUT.mul_b_valid && U_DUT.mul_b_ready))
                asymmetric_accept_cycles = asymmetric_accept_cycles + 1;
            if ((U_DUT.add_a_valid && U_DUT.add_a_ready) != (U_DUT.add_b_valid && U_DUT.add_b_ready))
                asymmetric_accept_cycles = asymmetric_accept_cycles + 1;
            if (U_DUT.a_sent_reg && (U_DUT.mul_a_valid || U_DUT.add_a_valid || U_DUT.log_a_valid))
                $fatal(1, "ACCEPTED_A_WAS_RESENT");
            if (U_DUT.b_sent_reg && (U_DUT.mul_b_valid || U_DUT.add_b_valid))
                $fatal(1, "ACCEPTED_B_WAS_RESENT");
            if (U_DUT.operation_sent_reg && U_DUT.add_operation_valid)
                $fatal(1, "ACCEPTED_OPERATION_WAS_RESENT");
        end else hold_active = 0;
    end

    // The actual IP outputs must remain stable across disabled clock edges.
    // Capture before the edge's NBA updates and check after those updates.
    always @(posedge clk) begin : CHECK_VENDOR_CLOCK_ENABLE
        logic mul_paused, add_paused, log_paused;
        logic [34:0] mul_pins;
        logic [35:0] add_pins;
        logic [33:0] log_pins;
        mul_paused = rst_n && !U_DUT.mul_aclken;
        add_paused = rst_n && !U_DUT.add_aclken;
        log_paused = rst_n && !U_DUT.log_aclken;
        mul_pins = {U_DUT.mul_a_ready, U_DUT.mul_b_ready, U_DUT.mul_result_valid, U_DUT.mul_result_data};
        add_pins = {U_DUT.add_a_ready, U_DUT.add_b_ready, U_DUT.add_operation_ready,
                    U_DUT.add_result_valid, U_DUT.add_result_data};
        log_pins = {U_DUT.log_a_ready, U_DUT.log_result_valid, U_DUT.log_result_data};
        if (mul_paused && (U_DUT.mul_a_valid || U_DUT.mul_b_valid || U_DUT.mul_result_ready))
            $fatal(1, "MUL_HANDSHAKE_WHILE_CLOCK_DISABLED");
        if (add_paused && (U_DUT.add_a_valid || U_DUT.add_b_valid || U_DUT.add_operation_valid || U_DUT.add_result_ready))
            $fatal(1, "ADD_HANDSHAKE_WHILE_CLOCK_DISABLED");
        if (log_paused && (U_DUT.log_a_valid || U_DUT.log_result_ready))
            $fatal(1, "LOG_HANDSHAKE_WHILE_CLOCK_DISABLED");
        #1;
        if (mul_paused) begin
            mul_paused_cycles = mul_paused_cycles + 1;
            if ({U_DUT.mul_a_ready, U_DUT.mul_b_ready, U_DUT.mul_result_valid, U_DUT.mul_result_data} !== mul_pins)
                $fatal(1, "MUL_OUTPUT_CHANGED_WHILE_CLOCK_DISABLED");
        end
        if (add_paused) begin
            add_paused_cycles = add_paused_cycles + 1;
            if ({U_DUT.add_a_ready, U_DUT.add_b_ready, U_DUT.add_operation_ready,
                 U_DUT.add_result_valid, U_DUT.add_result_data} !== add_pins)
                $fatal(1, "ADD_OUTPUT_CHANGED_WHILE_CLOCK_DISABLED");
        end
        if (log_paused) begin
            log_paused_cycles = log_paused_cycles + 1;
            if ({U_DUT.log_a_ready, U_DUT.log_result_valid, U_DUT.log_result_data} !== log_pins)
                $fatal(1, "LOG_OUTPUT_CHANGED_WHILE_CLOCK_DISABLED");
        end
    end

    task automatic transaction(input integer id, input logic [1:0] operation,
        input logic [31:0] a, input logic [31:0] b,
        input integer response_stall);
        longint requested_at, seen_at, accepted_at;
        logic [31:0] observed;
        begin
            @(negedge clk);
            req_valid = 1;
            req_op = operation;
            req_a = a;
            req_b = b;
            rsp_ready = 0;
            do @(posedge clk); while (!req_ready);
            requested_at = cycles;
            @(negedge clk);
            req_valid = 0;
            do @(posedge clk); while (!rsp_valid);
            seen_at = cycles;
            observed = rsp_data;
            repeat(response_stall) begin
                @(posedge clk);
                if (!rsp_valid || rsp_data !== observed || req_ready)
                    $fatal(1, "LONG_OUTPUT_STALL_OR_OVERLAPPING_REQUEST");
            end
            @(negedge clk); rsp_ready = 1;
            @(posedge clk);
            if (!rsp_valid || rsp_data !== observed) $fatal(1, "RESPONSE_ACCEPT_LOST");
            accepted_at = cycles;
            $fwrite(output_fd, "%0d %0d %08h %08h %08h %0d %0d %0d\n",
                    id, operation, a, b, observed, requested_at, seen_at, accepted_at);
            @(negedge clk); rsp_ready = 0;
            completed = completed + 1;
        end
    endtask

    task automatic reset_inflight_transaction;
        begin
            @(negedge clk);
            req_valid = 1; req_op = 2'b00; req_a = 32'h40800000; req_b = 32'h40a00000;
            do @(posedge clk); while (!req_ready);
            @(negedge clk);
            req_valid = 0;
            repeat(3) @(negedge clk);
            if (U_DUT.a_sent_reg !== 1'b1 || U_DUT.b_sent_reg !== 1'b1 || rsp_valid)
                $fatal(1, "RESET_TEST_DID_NOT_REACH_INFLIGHT_OPERATION");
            rst_n = 0;
            repeat(8) @(negedge clk);
            rst_n = 1;
            repeat(40) begin
                @(negedge clk);
                if (rsp_valid || !req_ready || o_error) $fatal(1, "RESET_DID_NOT_ABORT_INFLIGHT_TRANSACTION");
            end
        end
    endtask

    initial begin
        if (!$value$plusargs("OUTPUT=%s", output_path)) output_path = ".";
        output_fd = $fopen({output_path, "/alu_results.txt"}, "w");
        checks_fd = $fopen({output_path, "/alu_protocol.txt"}, "w");
        if (!output_fd || !checks_fd) $fatal(1, "ALU_OUTPUT_OPEN");
        repeat(8) @(negedge clk);
        rst_n = 1;
        repeat(8) @(negedge clk);
        transaction(0, 2'b00, 32'h3fc00000, 32'hc0000000, 37); // 1.5 * -2
        transaction(1, 2'b01, 32'h3f400000, 32'h3e800000, 0); // .75 + .25
        transaction(2, 2'b10, 32'h3f800000, 32'h3f733333, 19); // 1 - binary32(.95)
        transaction(3, 2'b01, 32'h3f800000, 32'hbf800000, 3); // signed cancellation
        transaction(4, 2'b01, 32'h3f800000, 32'h33800000, 0); // nearest-even tie
        transaction(5, 2'b00, 32'h80000000, 32'h3f800000, 11); // signed zero
        transaction(6, 2'b11, 32'h3f800000, 32'h00000000, 31); // ln(1)
        transaction(7, 2'b11, 32'h2b8cbccc, 32'h00000000, 0); // ln(frozen floor)
        reset_inflight_transaction();
        transaction(8, 2'b00, 32'h40000000, 32'h40400000, 53); // recovery:2*3
        transaction(9, 2'b10, 32'hbf800000, 32'hbf800000, 0);
        repeat(40) @(negedge clk);
        if (request_count != 11 || response_count != 10 || completed != 10)
            $fatal(1, "ALU_TRANSACTION_COUNT req=%0d rsp=%0d complete=%0d", request_count, response_count, completed);
        if (mul_paused_cycles < 100 || add_paused_cycles < 100 || log_paused_cycles < 100)
            $fatal(1, "ALU_CLOCK_ENABLE_COVERAGE_MISSING");
        $fwrite(checks_fd, "PASS requests=%0d responses=%0d aborted=1 completed=%0d asymmetric_accept_cycles=%0d reset_cycles=8 mul_paused_cycles=%0d add_paused_cycles=%0d log_paused_cycles=%0d\n",
                request_count, response_count, completed, asymmetric_accept_cycles,
                mul_paused_cycles, add_paused_cycles, log_paused_cycles);
        $fclose(output_fd); $fclose(checks_fd);
        $display("ALU_PROTOCOL_PASS completed=%0d asymmetric_cycles=%0d", completed, asymmetric_accept_cycles);
        $finish;
    end
endmodule
