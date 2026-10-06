"""Independent arithmetic/oracle checks, not a tolerance waiver for MFCC accuracy."""
import math
import random
import unittest
from fractions import Fraction
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from software.fixed_model.full_integer import rne_div, rne_shift, preemphasis_pcm, choose_bfp_integer, quantize_fft_input, log_integer, dct_integer, LOG_FLOOR_Q24
from scripts.run_fixed_full_model import generate_coefficients

class IntegerModelTests(unittest.TestCase):
    def test_portable_log_product_without_int128(self):
        rng=random.Random(91)
        for a in [-91*(1<<30),-40*(1<<30),-(1<<34),-1,0,1,1<<34,21*(1<<30)]+[rng.randrange(-40*(1<<30),22*(1<<30)) for _ in range(10000)]:
            high,low=divmod(a,64)
            small_product=low*744261118
            base=high*744261118+small_product//64
            self.assertLess(abs(base),1<<63)
            q,r=divmod(base,1<<30)
            up=r>(1<<29) or (r==(1<<29) and (small_product%64!=0 or q&1))
            self.assertEqual(q+int(up),rne_shift(a*744261118,36))
    def test_signed_ties_and_boundaries(self):
        for denominator in (2,4,20,32768,1<<30):
            for q in (-32768,-4,-3,-2,-1,0,1,2,3,32767):
                for r in (0,denominator//2-1,denominator//2,denominator//2+1,denominator-1):
                    v=q*denominator+r
                    self.assertEqual(rne_div(v,denominator),round(Fraction(v,denominator)))
    def test_preemphasis_continuous_exact_alpha(self):
        pcm=[0,-32768,32767,-1,1,0]*100
        got=preemphasis_pcm(pcm)
        prev=0
        for sample,value in zip(pcm,got):
            oracle=round((Fraction(sample)-Fraction(19,20)*prev)*32768)
            self.assertEqual(value,oracle)
            prev=sample
    def test_rtl_preemphasis_reciprocal_decomposition(self):
        for magnitude in range(65536):
            self.assertEqual((magnitude*52429)>>18,magnitude//5)
        for previous in range(-32768,32768):
            quotient,remainder=divmod(abs(previous),5)
            correction=(quotient<<13)+[0,1638,3277,4915,6554][remainder]
            if previous<0:correction=-correction
            for sample in (-32768,0,32767):
                actual=(sample-previous)*32768+correction
                self.assertEqual(actual,rne_div((20*sample-19*previous)*32768,20))
    def test_bfp_boundaries(self):
        self.assertEqual(choose_bfp_integer([0]),(0,False))
        for s in range(-2,17):
            threshold=31949 << (16-s)
            for peak in (threshold-1,threshold,threshold+1):
                expected=max([x for x in range(-2,25) if Fraction(peak)*Fraction(2)**(x-16)<=31949] or [-2])
                self.assertEqual(choose_bfp_integer([peak,-peak])[0],expected)
        self.assertEqual(quantize_fft_input([-32768,32768],16),([-32767,32767],2))
    def test_rtl_narrow_quantization_and_round_pipeline(self):
        rng=random.Random(63)
        for s in range(-2,25):
            mask=0x3ffff>>(s+2)
            half=0x20000>>(s+2)
            values=[-(1<<31),(1<<31)-1,-32768,-1,0,1,32767]
            if s<16:
                scale=1<<(16-s)
                values += [q*scale+r for q in (-32768,-3,-2,-1,0,1,2,3,32767)
                           for r in (scale//2-1,scale//2,scale//2+1) if -(1<<31)<=q*scale+r<(1<<31)]
            values += [rng.randrange(-(1<<31),1<<31) for _ in range(2000)]
            for value in values:
                quotient=value>>(16-s) if s<=16 else value<<(s-16)
                self.assertGreaterEqual(quotient,-(1<<39))
                self.assertLess(quotient,1<<39)
                remainder=value&mask
                rounded=quotient+int(s<16 and (remainder>half or (remainder==half and quotient&1)))
                got=max(-32767,min(32767,rounded))
                self.assertEqual(got,quantize_fft_input([value],s)[0][0])
    def test_log_all_floor_boundaries_and_random(self):
        rng=random.Random(20261004)
        floor=Fraction(1e-12)
        worst=0
        for s in range(-2,25):
            exponent=-43-2*s
            threshold=(floor*Fraction(2)**(-exponent)).__ceil__()
            self.assertEqual(threshold,(Fraction(1,10**12)*Fraction(2)**(-exponent)).__ceil__())
            for value in [0,1,threshold-1,threshold,threshold+1,(1<<60)-1]+[rng.randrange(1<<60) for _ in range(200)]:
                actual,floored=log_integer(value,exponent)
                self.assertEqual(floored,Fraction(value)*Fraction(2)**exponent < floor)
                reference=math.log(max(math.ldexp(value,exponent),1e-12))
                difference=abs(actual/2**24-reference)
                worst=max(worst,difference)
                self.assertLess(difference,8e-8)
        print(f"independent log oracle maximum absolute error={worst:.12g}")
    def test_dct_exact_integer_and_bound(self):
        coef,_=generate_coefficients()
        rng=random.Random(7)
        for frame in [[LOG_FLOOR_Q24]*26]+[[rng.randint(LOG_FLOOR_Q24,244000000) for _ in range(26)] for _ in range(100)]:
            values=dct_integer(frame,coef['dct_q30'])
            for row,actual in zip(coef['dct_q30'],values):
                exact=sum(Fraction(x)*y for x,y in zip(frame,row))/2**30
                self.assertEqual(actual,round(exact))
                self.assertLess(abs(exact*2**30),1<<62)

if __name__=='__main__':unittest.main(verbosity=2)
