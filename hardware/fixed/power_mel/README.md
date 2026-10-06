# Integer Power/Mel RTL

`fixed_spectral_tail` accepts a reservation (`i_frame_start && o_frame_ready`)
before a 512-bin natural-order FFT stream. The FFT stream has **no ready**.
One frame is in flight; a new frame may only be reserved after the previous
band 25 output is accepted. Metadata is latched at reservation. Reset discards
the reservation, buffered validity, arithmetic state and pending output.

Input real/imaginary values are signed W20/F19. Their squares use full signed
W40 products, the sum is unsigned W40 (mathematical maximum 2^39). Bin 0..256
power is stored; bins 257..511 are consumed and checked for order and last.
Power means `psum * 2^(-27-2*s)`. No rounding is performed in this module.
Two pipeline stages register the incoming complex value and then its two
full W40 squares. Valid, bin, last and FFT overflow advance through the same
two stages. The sum is written to Power RAM on the third sampling edge,
and the Power debug output becomes valid after that edge. The capture FSM
checks the delayed transaction, so reset and malformed early-last discard
pending work without exposing a partial Mel frame. No incoming FFT clock
is stalled. This splits FFT reorder BRAM, multiplication and sum/RAM into
separate timing paths without changing the numerical contract.

Mel coefficients are unsigned W17/F16, generated exclusively by
`software.fixed_model.coeffs.quantize_mel_filterbank(16)`. Products are unsigned
W57, accumulators and outputs unsigned W60. A W61 addition exposes carry;
an unexpected carry wraps at W60 and raises the frame overflow flag. The
fixed coefficient table's bound is below 2^60 even for every FFT component
at -2^19. Mel energy means `value * 2^(-43-2*s)`.

Memory architecture is selected before implementation to respect the RTL
rules: AMD XPM block RAM primitives provide a 512x40 single-port power RAM
(257 live words) and an 8192x17 coefficient ROM (6682 live words). Both have
one-cycle synchronous reads. Vendor XPM initialization is outside authored
RTL; the authored modules contain no `initial`, array reset or next-array
copy. ROM initialization uses the versioned generated `mel_fw16.mem` file.
RAM/ROM output registers are not reset; FSM validity hides their contents.
One READ/MULTIPLY/ACCUMULATE sequence is in flight. Thus no response queue or
ping-pong bank is needed; buffering an entire accepted FFT frame decouples
the non-stallable producer from arbitrarily stalled Mel output.

Nominal processing after the delayed last FFT bin is 26*(257*3+1)=20,072 clocks,
with two pipeline clocks between external FFT acceptance and that last bin,
plus output stalls. This is a cycle budget, not a timing-closure claim.
Output valid/data/band/last/frame/BFP/error are held while ready is low.
Malformed ordering or last aborts a frame and drains until its last; no
partial Mel output is produced. Frame errors clear on the next reservation.
Reset is the recovery mechanism for a stream that never supplies last.

`scripts/run_fixed_power_mel.py` snapshots inputs, sources and coefficients
under a fresh build directory, executes actual Vivado 2024.2 simulation,
then synthesis and place/route for xc7z020clg400-1 at a 100 MHz constraint.
The reports distinguish bit matching, mapping and timing outcomes.
