"""Independent bit-exact model of reference_code/previous_fft R2^2SDF FFT.

Derived by reading the RTL only (rtl/r22sdf/*, rtl/common/*, rtl/top/*,
rtl/reorder/*), not from the project's MATLAB model.  Block-level form of the
SDF pipeline:

  group(L):  stage-I radix-2 DIF butterfly (16 -> 17 bit, exact)
             stage-II butterfly on quarters, -j on the upper half (17 -> 18 bit)
             twiddle W_L^(d*n), d = [0,2,1,3] per quarter, 18x16 >>15 -> 18
             group shift >>2 -> 16 bit
  repeat with L/4 while L >= 4; a trailing L == 2 does one stage-I + >>1.

All shifts are convergent (ties-to-even) and all narrowing wraps (no
saturation), matching rtl/common/fixed_round_shift.sv.
"""
import numpy as np

TW_W = 16
TW_FRAC = 15


def load_twiddle_rom(path):
    """twiddle_1024_w16.mem: one 8-hex-digit word per line, {re[15:0], im[15:0]}."""
    re = np.zeros(1024, dtype=np.int64)
    im = np.zeros(1024, dtype=np.int64)
    with open(path) as fh:
        lines = [ln.strip() for ln in fh if ln.strip()]
    assert len(lines) == 1024, len(lines)
    for a, ln in enumerate(lines):
        word = int(ln, 16)
        r = word >> 16
        i = word & 0xFFFF
        re[a] = r - 65536 if r >= 32768 else r
        im[a] = i - 65536 if i >= 32768 else i
    return re, im


def wrap_signed(value, width):
    m = 1 << width
    half = m >> 1
    w = ((value + half) % m) - half
    return w, (w != value)


def round_shift(value, shift, out_w):
    """rtl/common/fixed_round_shift.sv: convergent round then wrap to out_w."""
    if shift == 0:
        q = value
    else:
        floor_q = value >> shift            # arithmetic / floor
        rem = value & ((1 << shift) - 1)
        round_bit = (rem >> (shift - 1)) & 1
        sticky = rem & ((1 << (shift - 1)) - 1)
        inc = 1 if (round_bit and (sticky != 0 or (floor_q & 1))) else 0
        q = floor_q + inc
    return wrap_signed(q, out_w)


class Flags:
    def __init__(self):
        self.overflow = False


def _group(block_re, block_im, L, tw_re, tw_im, flags):
    """One complete R2^2 group on a length-L block. 16-bit in, 16-bit out."""
    h = L // 2
    q = L // 4
    # --- stage I: bf_type1, 16 -> 17, CALC_W == OUT_W so exact ---
    a_re = [0] * L
    a_im = [0] * L
    for n in range(h):
        a_re[n], ov = wrap_signed(block_re[n] + block_re[n + h], 17)
        flags.overflow |= ov
        a_im[n], ov = wrap_signed(block_im[n] + block_im[n + h], 17)
        flags.overflow |= ov
        a_re[h + n], ov = wrap_signed(block_re[n] - block_re[n + h], 17)
        flags.overflow |= ov
        a_im[h + n], ov = wrap_signed(block_im[n] - block_im[n + h], 17)
        flags.overflow |= ov
    # --- stage II: bf_type2, 17 -> 18, wraps at 18 ---
    c_re = [0] * L
    c_im = [0] * L
    for n in range(q):
        # lower half block: no rotation
        d_re, d_im = a_re[n], a_im[n]
        x_re, x_im = a_re[n + q], a_im[n + q]
        for idx, (rr, ii) in ((n, (d_re + x_re, d_im + x_im)),
                              (q + n, (d_re - x_re, d_im - x_im))):
            c_re[idx], ov = wrap_signed(rr, 18); flags.overflow |= ov
            c_im[idx], ov = wrap_signed(ii, 18); flags.overflow |= ov
        # upper half block: rotate_minus_j(x) = (x_im, -x_re)
        d_re, d_im = a_re[2 * q + n], a_im[2 * q + n]
        x_re, x_im = a_re[3 * q + n], a_im[3 * q + n]
        r_re, r_im = x_im, -x_re
        for idx, (rr, ii) in ((2 * q + n, (d_re + r_re, d_im + r_im)),
                              (3 * q + n, (d_re - r_re, d_im - r_im))):
            c_re[idx], ov = wrap_signed(rr, 18); flags.overflow |= ov
            c_im[idx], ov = wrap_signed(ii, 18); flags.overflow |= ov
    # --- twiddle + group shift ---
    out_re = [0] * L
    out_im = [0] * L
    branch = [0, 2, 1, 3]
    for quarter in range(4):
        d = branch[quarter]
        for n in range(q):
            idx = quarter * q + n
            zr, zi = c_re[idx], c_im[idx]
            if q == 1:
                # delay_log2 == 0 -> L == 4 terminal group, multiplier bypassed
                mr, mi = zr, zi
            else:
                # rtl/r22sdf/r22sdf_runtime_twiddle_addr_gen.sv
                addr = ((d * n) << (8 - (q.bit_length() - 1))) & 0x3FF
                wr, wi = int(tw_re[addr]), int(tw_im[addr])
                full_re = zr * wr - zi * wi
                full_im = zr * wi + zi * wr
                mr, ov = round_shift(full_re, 15, 18); flags.overflow |= ov
                mi, ov = round_shift(full_im, 15, 18); flags.overflow |= ov
            out_re[idx], ov = round_shift(mr, 2, 16); flags.overflow |= ov
            out_im[idx], ov = round_shift(mi, 2, 16); flags.overflow |= ov
    return out_re, out_im


def fft_fixed(x_re, x_im, tw_re, tw_im):
    """Bit-exact core output, in BIT-REVERSED bin order, plus overflow flag."""
    N = len(x_re)
    log2n = N.bit_length() - 1
    assert 1 << log2n == N and 3 <= log2n <= 10
    cur_re = [int(v) for v in x_re]
    cur_im = [int(v) for v in x_im]
    flags = Flags()
    L = N
    while L >= 4:
        nxt_re = [0] * N
        nxt_im = [0] * N
        for base in range(0, N, L):
            br, bi = _group(cur_re[base:base + L], cur_im[base:base + L],
                            L, tw_re, tw_im, flags)
            nxt_re[base:base + L] = br
            nxt_im[base:base + L] = bi
        cur_re, cur_im = nxt_re, nxt_im
        L //= 4
    if L == 2:
        nxt_re = [0] * N
        nxt_im = [0] * N
        for base in range(0, N, 2):
            for comp, cur in ((0, cur_re), (1, cur_im)):
                s, ov = wrap_signed(cur[base] + cur[base + 1], 17)
                flags.overflow |= ov
                d, ov = wrap_signed(cur[base] - cur[base + 1], 17)
                flags.overflow |= ov
                s, ov = round_shift(s, 1, 16); flags.overflow |= ov
                d, ov = round_shift(d, 1, 16); flags.overflow |= ov
                if comp == 0:
                    nxt_re[base], nxt_re[base + 1] = s, d
                else:
                    nxt_im[base], nxt_im[base + 1] = s, d
        cur_re, cur_im = nxt_re, nxt_im
    return cur_re, cur_im, flags.overflow


def bit_reverse(index, width):
    out = 0
    for b in range(width):
        if index & (1 << b):
            out |= 1 << (width - 1 - b)
    return out


def fft_fixed_natural(x_re, x_im, tw_re, tw_im):
    """Natural bin order, as produced by rtl/reorder/natural_reorder_pingpong.sv."""
    N = len(x_re)
    log2n = N.bit_length() - 1
    br, bi, ov = fft_fixed(x_re, x_im, tw_re, tw_im)
    nr = [0] * N
    ni = [0] * N
    for ordinal in range(N):
        bin_index = bit_reverse(ordinal, log2n)
        nr[bin_index] = br[ordinal]
        ni[bin_index] = bi[ordinal]
    return nr, ni, ov
