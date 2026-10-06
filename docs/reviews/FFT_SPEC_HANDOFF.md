# FFT 규격 반영안 (Codex 인계)

작성: 2026년 10월 4일. **3판: 고정소수점 Q0/Q1 실험 결과를 반영한 정정판.**
작성자: Claude Code.
대상 문서: `docs/MFCC_SPEC.md` (2026-10-04 판).
상세 검증 근거: [FFT_REUSE_REVIEW.md](FFT_REUSE_REVIEW.md),
[FIXED_POINT_QUANTIZATION_PLAN.md](FIXED_POINT_QUANTIZATION_PLAN.md),
[FIXED_POINT_PILOT.md](FIXED_POINT_PILOT.md).

3판 정정 항목: C9(FIFO 깊이 주장 철회), C12(BFP 지수를 반영한 로그 floor),
C13(오차 전력을 에너지 하한이라 부르지 않음), C14(저음량 프레임 제외 제안 삭제),
C15(Mel 계수 양자화 오차 측정), B5(누산 tight bound 52비트).

## 0. 이 문서의 성격

`MFCC_SPEC.md`는 **수정하지 않았다.** 이 문서는 반영안이고, 반영 판단은 Codex가 한다.

`MFCC_SPEC.md:145`의 "검토가 들어오면 위 FFT 계약과 충돌을 확인한 뒤 규격을 갱신한다"에
대한 답이다. **충돌은 없다.** `:121`, `:139`가 FFT 검토로 넘긴 미확정 항목을 채우고,
`:123`, `:128`, `:156`의 제안에 조건을 보강한다.

범위 제한:

- **FFT 단독** 검증 결과다. power/Mel/log/DCT는 구현하지 않았다.
- **실제 보드 검증은 없다.** 비트스트림·JTAG·UART·DMA 어느 것도 실행하지 않았다.
- **실제 음성으로 MFCC 정확도를 측정하지 않았다.** 5장의 Mel 관련 수치는 합성
  가우시안 잡음과 내가 작성한 float64 필터뱅크에서 나온 것이며, Codex 규격의
  필터뱅크·로그 하한과 비트 단위로 맞추지 않았다.
- 검증 도구는 **Vivado 2024.2**(주)와 **2020.2**(보존된 기존 증거)다.

---

## 1. 반영 상태 요약

| 상태 | 개수 | 뜻 |
|---|---:|---|
| **확정** | 9 | 실행한 증거로 확인됨. 지금 반영 가능 |
| **조건부** | 6 | 명시한 조건·입력군 안에서만 확인됨. 조건을 함께 적어 반영 |
| **미검증** | 6 | 실행하지 않았음. 규격에 단정으로 쓰면 안 됨 |

---

## 2. 항목별 반영안

### 2.1 확정 항목

| # | 현재 `MFCC_SPEC.md` | 제안 문구 | 근거 | 상태 |
|---|---|---|---|---|
| A1 | `:121` "이번 선택인 N=512의 비트정확·연속 프레임 시험과 누적 shift S는 별도 FFT 검토에서 확정해야 한다" | "N=512에서 기존 `fft_stream_top`의 **누적 shift S = log2 N = 9**이고, 출력은 **자연 순서 bin 0…511**이다. 독립 비트정확 모델과 24개 N=512 시나리오에서 불일치 0이며 Vivado 2024.2와 2020.2의 출력이 바이트 단위로 동일하다." | `claude-review-2024_2/logs/RESULTS_2024_2.txt`, `logs/check_probe_2024_2.txt`, `logs/check_probe2_2024_2.txt` | **확정** |
| A2 | `:139` "FFT 입출력 … N512 shift/순서/overflow/단독 drain 검증 필요" | "검증 완료. shift=9, 자연 순서, overflow 플래그가 비트모델과 일치, 단독(1프레임) drain 정상." | 위와 동일. 단독 프레임은 id 1–6, 11, 12, 14, 16, 17, 24–26, 28–31 | **확정** |
| A3 | `:40` FFT 행 (float IP 기준) | "고정소수점 비교군의 FFT는 `fft_stream_top`이며 **float32 `fft_block`(xfft)의 대체가 아니다.** 부동소수점 IP 비교군은 xfft를 따로 생성·검증한다." | `github_mfcc/.../power_spectrum.v:79` | **확정** |
| A4 | `:128` `F ≈ FFT(u)/(2*2^S)`, `P = 2048*(Re(F)²+Im(F)²)` | **그대로 유지.** 수식이 맞다. S=9 확인으로 계수 2048이 확정된다. 정수 형태 `P = (out_re²+out_im²)/2^19`가 수치적으로 **동일**함을 확인했다. | `claude-review/logs/power_mel_widths.txt` ("identical : True") | **확정** |
| A5 | `:143` overflow 정책 "wrap+sticky를 비트모델에 그대로 반영" | **유지.** 추가: "`out_overflow`는 **프레임 단위** 플래그이며 해당 프레임의 모든 출력 beat에서 1로 재생된다. 프레임 간에 누적되지 않으므로 프레임별로 수집한다." | `fft_r22sdf_core.sv:155`, `natural_reorder_pingpong.sv`(`bank_frame_overflow`), 3.6 of 리뷰 | **확정** |
| A6 | `:156` "기존 FFT에는 output-ready가 없으므로 …" | "기존 FFT에는 output-ready가 없고, **`fft_r22sdf_core`에는 하류 정지 입력 포트 자체가 없다.** 재정렬 버퍼의 읽기측도 멈출 수 없다. 따라서 코어를 세우는 방식은 다단 RTL 수정이며, 지금은 **출력 FIFO + 프레임 수락 제한**만이 설계 추가 없이 가능한 대응이다." | `fft_r22sdf_core.sv` 포트 목록, `natural_reorder_pingpong.sv:178-205`, `fft_stream_top.sv:64,122` | **확정** |
| A7 | `:156` "DMA S2MM을 먼저 시작하는 것만으로 임의 backpressure 지원이 증명되지 않는다" | "판정 기준은 **정지 중 데이터·`tvalid`·`tlast`를 수락 시점까지 유지하는가**이다. `fft_axi_stream_wrapper`는 hold/skid 레지스터가 없어 **유지하지 않는다**: beat 데이터 소실, handshake 없는 `tvalid` 하강(AXI 규약 위반), `tlast` 소실로 프레임 경계 유실." | `fft_axi_stream_wrapper.sv:83-86,158-159` | **확정** |
| A8 | `:158` (신규 MFCC 출력 AXI) "stalled 데이터·index·TLAST는 유지한다" | **유지.** 추가: "기존 `fft_axi_stream_wrapper`는 이 규칙을 지키지 않으므로 **참조 구현으로 쓰지 않는다.**" | A7과 동일 | **확정** |
| A9 | `:159` "PL 100 MHz 단일 연산 도메인과 active-low 동기 해제 reset" | **유지.** 추가: "기존 FFT는 단일 클록 도메인이고 `rst_n`은 **동기** active-low다(CDC 없음). 리셋 구간에 클록이 공급되어야 한다. 또 **리셋 후 `cfg_apply` 1 cycle 펄스 전에는 입력이 전혀 수락되지 않는다.**" | 전 모듈 `always_ff @(posedge clk) if (!rst_n)`, `fft_stream_top.sv:75`, 지향 검사 D1/D2/D6 | **확정** |

### 2.2 조건부 항목 (조건을 함께 적어 반영)

| # | 현재 `MFCC_SPEC.md` | 제안 문구 | 근거 | 상태 |
|---|---|---|---|---|
| B1 | `:123` "`z=u/2`를 Q1.15로 반올림해 입력 … \|z\|≤0.975" | **유지하고 조건과 clamp를 추가:** "입력 Q1.15 정수를 **`[-32767, +32767]`로 clamp**해 `-32768` 코드를 배제한다. peak 0.975는 두 합성 적대 신호군(전 k full-scale 사각파 1,792 프레임, 무작위 ±A 부호 패턴 1,500 프레임, N=256/512/1024)에서 overflow 0이었다. **실제 음성과 pre-emphasis 경로는 이 재현 자료에 포함되지 않는다.**" | `claude-review/logs/sweep_clamp_check.txt` (seed 20261004, 13,168 프레임), `sweep_clamp_check.py` | **조건부** |
| B2 | `:123` "모든 fixed stage의 overflow를 기록하고 무시하지 않는다" | **유지하고 이유를 수치로:** "wrap이므로 피해가 크다. 실측 — 실수 full-scale 오버플로 프레임 NMSE −15.1 dB / 최대 오차 513 LSB, 복소 full-scale −2.5 dB / 4,841 LSB. 같은 프레임에 1비트 여유를 주면 −61.8 dB / 1.2 LSB." | 리뷰 3.6 (시나리오 id 24/25/29) | **조건부** (실측값은 그 세 프레임) |
| B3 | `:138` pre-emphasis·윈도 행 "FFT 전 /2" | **유지.** 추가: "`-32768` 배제 clamp 위치를 FFT 입력 직전으로 명시한다. 입력 폭·소수부·반올림 위치는 여전히 미확정이다." | B1 | **조건부** |
| B4 | `:92` "epsilon은 **정규화된 P와 E의 단위**이다. FFT의 축소된 정수값에 같은 숫자를 그대로 적용하지 않는다" | **유지하고 보강:** 5.2~5.3 참고. floor는 **BFP 지수까지 반영한 `E_m = T_m * 2^-(19+2s+Fw)`**에서 비교하며, **양수 정수 `T_m`도 floor 아래일 수 있다**(s=10에서 경계 `T=36029`). 저에너지 구간에서 fixed와 float은 floor 선택만으로 일치시킬 수 없다. **[3판 정정 C13]** 1·2판이 쓴 "band 에너지 바닥 1.7e-6~1.3e-5"는 FFT 출력 RMS에서 유도한 특정 조건의 오차 전력이며, 모든 신호의 실제 Mel 에너지 하한이 아니다 | `verification/fixed/test_power_mel.py`, `FIXED_POINT_PILOT.md` 4장 | **확정**(floor 위치) / **조건부**(저에너지 거동의 크기) |
| B5 | `:140` power·Mel 행 "제곱합 확대 폭 및 누적 guard bits 유지, 스케일 메타데이터 보존" | 5장의 구체 폭 제안으로 교체 가능(psum unsigned 32, 가중치 unsigned `Fw+1`, 누산 **tight 52비트 @Fw=16**(loose 56), P 소수 19, E 소수 `19+2s+Fw`). **[3판 정정]** 1·2판은 누산을 56비트로만 적었다. 실제 계수표에서 유도한 tight bound는 52비트다. 폭은 `verification/fixed/test_power_mel.py`에서 검사했으나 **RTL로는 검증하지 않았다** | `FIXED_POINT_QUANTIZATION_PLAN.md` 1.4~1.5, `verification/fixed/test_power_mel.py` | **조건부** |
| B6 | `:140` Mel 가중치 소수부 | **Fw=16은 후보일 뿐이다.** 계수 양자화만의 기여를 Fw 12~20에서 측정했다(Fw=16: `max\|Δln E\| = 2.294e-05` nats). **[3판 정정 C15]** 1·2판은 Mel 계수 양자화 오차를 측정하지 않고 Fw=16을 제안했다 | `FIXED_POINT_QUANTIZATION_PLAN.md` 1.5, `verification/fixed/test_coeffs.py` | **조건부** |

### 2.3 미검증 항목 (규격에 단정으로 쓰지 말 것)

| # | 항목 | 이유 |
|---|---|---|
| C1 | FFT를 포함한 전체 MFCC 정확도 | power/Mel/log/DCT 미구현. 실제 음성 미사용 |
| C2 | 실제 보드에서의 FFT 동작 | 비트스트림·XSA·BSP·ELF·JTAG·UART·DMA 전부 미실행. ZYBO board preset 미확보 |
| C3 | PS7·AXI 포함 블록 디자인의 100 MHz 타이밍 | OOC만 실행. 2024.2 OOC 여유가 **+0.106 ns**로 줄었으므로 반드시 재확인 |
| C4 | AXI 정지 시 손실의 정량 | sticky 비트 상승만 확인. 2024.2에서 AXI TB 미실행 |
| C5 | 출력 FIFO / 전역 stall의 구현·검증 | 6장은 설계 제안이며 RTL 없음 |
| C6 | BFP의 실제 음성 효과 | 8장 참고. 합성 잡음에서만 시험 |

---

## 3. N=512 수치 계약 (확정)

| 항목 | 값 | 근거 |
|---|---|---|
| 변환 | 순방향 복소 FFT, `W = exp(-j2πk/N)` | `export_twiddle_rom.m:22` + ROM 1,024 entry 독립 재계산 전부 일치 |
| 길이 | N = 512 (`cfg_log2_n = 9`) | `fft_stream_top.sv:66-67` |
| **입력 순서** | 자연 순서 n = 0…511, `in_last`를 n = 511에 | `fft_r22sdf_core.sv:190,198` |
| **출력 순서** | **자연 순서 bin k = 0…511.** `out_index`가 bin 번호 | 재정렬 버퍼 + 인덱스 오류 0 |
| **총 shift S** | **9** (완전 group 4개 × 2 + 단독 Type-I 1개 × 1) | `r22sdf_runtime_group_quantize.sv:166,171`, `r22sdf_runtime_group.sv:91,96` |
| **입력 형식** | signed 16비트 **Q1.15**, `in_re`/`in_im` 분리. MFCC는 `in_im = 0` | 포트, `r22sdf_runtime_group.sv:59` |
| **출력 형식** | signed 16비트 **Q1.15**, `out_re`/`out_im` 분리 | `fft_stream_top.sv` 포트 |
| twiddle | signed 16비트 Q1.15, 계수만 포화(+1.0 → 32767) | `twiddle_rom_sync.sv`, ROM 재계산 |
| 내부 | Type-I 17비트 Q2.15 → Type-II 18비트 Q3.15 → `>>15` → 18비트 → group `>>2` → 16비트 Q1.15 | 리뷰 3.3 |
| 반올림 | 전 구간 **ties-to-even** (`ROUND_CONVERGENT = 1`) | `fixed_round_shift.sv` |
| 오버플로 | **포화 아님. wrap + 검출** | `fixed_round_shift.sv:69,72` |
| 추가 재정렬 | **불필요** | 3.2 |
| bin 사용 | `out_index ≤ 256`만 power로 (257개) | `MFCC_SPEC.md:41`과 일치 |

직접 확인된 결정론적 값:

```
impulse  x[0]=32767, 나머지 0   -> 512개 bin 전부 out_re=64 = round(32767/512)
DC       x[n]=16384             -> out[0]=16384, 나머지 정확히 0
tone     x[n]=round(0.9*cos(2*pi*40*n/512)*32768)
                                 -> out[40]=out[472]=14744 (이상값 14746), 허수부 0
```

첫 출력 지연(첫 입력 수락 → 첫 출력): **1,048 cycle**. 마지막 출력까지 1,559 cycle.
연속 프레임에서 **1 complex sample/clock** 유지(8프레임 4,096 beat, 공백 0).

---

## 4. 입력 배율 u/2에서의 power 환산식과 적용 조건

### 4.1 식 (`MFCC_SPEC.md:128`과 동일, 정수 형태 추가)

`MFCC_SPEC.md`의 공통 정의는 `P_t[k] = (Re(X_t[k])² + Im(X_t[k])²)/512`,
`X_t[k] = Σ u_t[n]·exp(-j2πkn/512)` (무정규화 DFT, `:72`)다.
입력을 `z = u/2`로 넣으면 `F = out/2^15`에 대해

```
F       = DFT(u) / (2 * 2^S)                        , S = 9
P[k]    = (4 * 2^(2S) / 512) * (Re(F)^2 + Im(F)^2)
        = 2048 * (Re(F)^2 + Im(F)^2)
```

정수 형태(권장: 중간 부동소수 없이 그대로 계산 가능):

```
psum[k] = out_re[k]^2 + out_im[k]^2          // 정확, unsigned 32비트
P[k]    = psum[k] / 2^19                     // Q(13).19, 범위 [0, 4096)
```

두 형태가 수치적으로 동일함을 확인했다(`logs/power_mel_widths.txt`: `identical : True`).

일반 입력 배율 `α`(즉 `in = round(α·u·32768)`)에 대해서는

```
P[k] = N * (out_re^2 + out_im^2) / (α^2 * 2^30)
```

`α = 1/2`, `N = 512` → `P[k] = psum / 2^19`. BFP shift `s`를 추가로 걸었다면 `2^(2s)`로 더 나눈다.

### 4.2 적용 조건 (모두 성립해야 위 식이 맞다)

1. `cfg_log2_n = 9`가 수락된 상태(`active_log2_n == 9`). S는 활성 N에 의존한다.
2. `in_im = 0`. 복소 입력을 쓰면 오버플로 조건이 달라진다(리뷰 5.3).
3. 입력이 `round(0.5·u·32768)`이고 결과를 `[-32767, +32767]`로 clamp.
   clamp 때문에 `|α·u|`가 1에 매우 가까울 때 최대 1 LSB의 편차가 생기며,
   이는 환산식이 아니라 입력 양자화 오차로 분류한다.
4. 해당 프레임의 `out_overflow == 0`. 1이면 환산식이 의미 없다(wrap, 리뷰 3.6).
5. `input_frame_error == 0`, `reorder_frame_error == 0`,
   `bank_collision_error == 0`, FIFO 넘침 플래그(있다면) == 0.
6. 출력 beat을 하나도 놓치지 않았음. 놓치면 `out_index`에 빈 칸이 생긴다.
   FFT 자체는 `out_index`를 매 프레임 0…N-1의 순열로 내보낸다(29개 시나리오에서 확인).

---

## 5. power/Mel의 소수 비트·지수 유지와 로그 하한 적용

`MFCC_SPEC.md:92`의 "epsilon은 정규화된 P와 E의 단위이다. FFT의 축소된 정수값에 같은 숫자를
그대로 적용하지 않는다"는 원칙은 맞다. 아래는 그 원칙을 지키는 구체 방법 **제안**이다.
**이 폭은 계산으로 도출했고 RTL·비트모델로 검증하지 않았다(B5).**

### 5.1 제안 폭과 지수

| 단계 | 정수 표현 | 폭 | 소수 비트 | 비고 |
|---|---|---:|---:|---|
| FFT 출력 | `out_re`, `out_im` | signed 16 | 15 | Q1.15 |
| 제곱합 | `psum = out_re² + out_im²` | **unsigned 32** | — | 최대 2^31, **반올림 없음(정확)** |
| 정규화 power | `P = psum / 2^19` | — | **19** | 범위 [0, 4096), quantum 2^-19 = 1.907e-6 |
| Mel 가중치 | `w = round(B·2^16)` | **unsigned 17** | **16** | 0…65536. peak 1.0을 정확히 표현 |
| 곱 | `psum × w` | unsigned 49 | 35 | 반올림 없음 |
| 누산 | `E_int = Σ_k psum[k]·w[m,k]` | **unsigned 56** | **35** | 최악(257 bin × 최대 psum × 1.0) = 2^55.0 |
| 정규화 에너지 | `E = E_int / 2^35` | — | **35** | quantum 2.910e-11 |

핵심: **psum과 곱·누산에서 반올림을 하지 않는다.** `P`와 `E`는 "나누기"가 아니라
**소수 비트 수(19, 35)를 메타데이터로 들고 다니는 정수**다. 그러면 FFT 출력
양자화 외에 추가 손실이 없고, PC 비교 시 `E_int / 2^35`로 정확히 복원된다.
`MFCC_SPEC.md:153`의 "fixed는 signed int32 및 fractional_bits/scale 메타데이터를 별도 명시"와
같은 취지이며, 여기서는 그 값을 **19(P), 35(E)**로 제안한다.

### 5.2 공통 규격의 정규화 로그 하한 적용

**[3판 정정 C12] 1·2판은 "`E_int >= 1`이면 floor가 발동하지 않는다"고 적었다.
이는 `s = 0`(고정 α=1/2)에서만 참이고, BFP 경로에서는 틀리다.**

규격은 `L_t[m] = ln(max(E_t[m], 1e-12))`이고 `1e-12`는 **정규화 E의 단위**다.
정수 에너지 `T_m`의 실제 값은 **프레임 지수 `s`까지 반영한**

```
E_m = T_m * 2^-(19 + 2s + Fw)
```

이므로 비교는 반드시 이 값에서 해야 한다.

```
E_m < 1e-12   ->  L = ln(1e-12)
E_m >= 1e-12  ->  L = ln(T_m) + (mel_exp_log2) * ln 2
```

`s`에 따른 차이:

| s | mel 지수 | E의 1 LSB | `T_m >= 1`이면 floor 위인가 | floor 경계 `T_m` |
|---:|---:|---:|---|---:|
| 0 | -35 | 2.910e-11 | 예 (`1e-12`가 LSB보다 작음) | 1 |
| 5 | -45 | 2.842e-14 | 아니오 | 36 |
| 10 | -55 | 2.776e-17 | **아니오** | **36029** |

즉 **양수 정수 에너지도 floor 아래일 수 있다.** `T_m == 0`만 확인하는 shortcut은
BFP 경로에서 쓰면 안 된다. 검증: `verification/fixed/test_power_mel.py`
(지수 `-55`에서 경계 `T = 36029`를 정확 유리수 비교로 확인).
구현: `software/fixed_model/power_mel.py:log_mel_with_floor`.

`MFCC_SPEC.md:92`가 금지한 "0이면 정수 1" 구현은 여전히 쓰지 않는다.

### 5.3 저에너지 구간에서의 fixed·float 차이 (B4)

**[3판 정정 C13] 1·2판은 FFT 출력 RMS에서 유도한 오차 전력을 "band 에너지 바닥"이라고
불렀다. 그렇게 부르면 안 된다.** 그 값은 특정 합성 신호에서 측정한 FFT 출력 오차 RMS로부터
계산한 **그 조건에서의 오차 전력 추정**이며, 모든 신호의 실제 Mel 에너지 하한이 아니다.
실제 Mel 에너지는 신호에 따라 그보다 훨씬 작을 수도, 클 수도 있다.

측정 사실만 적으면 다음과 같다.

- 합성 Hamming 잡음에서 FFT 출력 오차는 bin당 복소 RMS **약 0.59 LSB(Q1.15)**였다
  (`claude-review/logs/sweep_range.txt`).
- α=1/2 환산에서 그 오차 전력은 `0.59²/2^19 ≈ 6.6e-7`에 해당한다. 이는 **그 신호의 그
  조건에서의 수치**다.
- 같은 합성 신호에서 α=1/2일 때 `P`가 0이 되는 bin 수는 입력 peak 1.0/0.25에서 0,
  0.0625에서 1, 0.01에서 58, 0.003에서 243개였다
  (`claude-review/logs/power_mel_widths.txt`).

실제 개발 음성에서의 거동은 추정이 아니라 측정으로 확인했다
([FIXED_POINT_PILOT.md](FIXED_POINT_PILOT.md) 4장): 고정 α=1/2에서 Mel 정수 0이
**2,307회(473/534 프레임)**, BFP에서 **102회(81프레임)**였고, BFP의 잔여 0은 band 0~4에
몰렸으며 그 셀의 기준 에너지는 **3.779e-09 … 7.425e-07**이었다.

**따라서 저에너지 구간에서 fixed와 float은 floor 선택만으로 일치시킬 수 없다.**
`E_m`이 floor 아래로 떨어진 셀에서 float 기준의 참 값은 `1e-12` 근처가 아니라
그보다 훨씬 큰 값일 수 있고, `ln(1e-12)`를 대입하면 셀당 8~13 nats의 오차가 생긴다.

제안(**Codex 결정 사항**):

1. **프레임·band 단위 유효성 플래그.** `E_m`이 floor 아래로 떨어진 band가 있으면 그
   프레임을 `quantization_floor_hit`으로 표시하고, 전체 통계와 **함께** 층화 집계한다.
   (`MFCC_SPEC.md:92`의 "오류 플래그·실패 기록"과 같은 취지)
2. **입력 정규화(BFP).** 8장. [FIXED_POINT_PILOT.md](FIXED_POINT_PILOT.md)에서
   효과를 측정했다.

**[3판 정정 C14] 1·2판에 있던 "입력 peak가 X dBFS 미만인 프레임은 비교 대상에서 제외"
제안을 삭제한다.** 저음량·에너지 0 프레임은 유효한 입력이고 주 비교에서 빼지 않는다
(`FIXED_POINT_DEVELOPMENT.md` 5장). 허용되는 것은 **전체 통계를 유지한 채 덧붙이는
층화 분석**뿐이다.

floor를 `1e-12`보다 크게 올려 fixed에 맞추는 것도 **권장하지 않는다** —
공통 규격을 fixed 구현에 맞춰 바꾸는 것이 되어 Python/C 기준이 흔들린다.

---

## 6. 제어·인터페이스 계약

### 6.1 reset / config

| 규칙 | 근거 |
|---|---|
| `rst_n` active-low **동기**. 리셋 구간에 클록 공급 필요. CDC 없는 단일 클록 | 전 모듈 |
| 리셋 직후 `cfg_ready=1`, `busy=0`, `active_log2_n=10`, **`in_ready=0`** | 지향 검사 D1 |
| **리셋 후 idle에서 `cfg_log2_n=9` + `cfg_apply` 1 cycle 펄스를 반드시 1회 발행.** 그 전에는 입력이 영구히 수락되지 않는다 | `fft_stream_top.sv:75`; D2에서 64 cycle 제시해도 수락 0 확인 |
| 범위 외 `cfg_log2_n`(≤2, ≥11)은 `cfg_reject=1`, `cfg_error=1`, `in_ready` 0 유지 | D3 |
| busy 중 `cfg_apply`는 거부되고 진행 중 프레임은 정상 완료 | D5 |
| `cfg_error`는 **다음 성공 설정까지 sticky** | `fft_stream_top.sv:152-157` |
| 프레임 중간 리셋 후 재설정하면 정상 복구(비트정확) | D6 |
| 크기 변경은 idle에서만. 512→256→512 확인 | id 30/31 |

### 6.2 입력 handshake

**샘플과 모든 카운터는 `in_valid && in_ready`가 동시에 1인 cycle에만 진행한다**
(`fft_r22sdf_core.sv:117`). `in_ready`는 항상 1이 아니다:
설정 전 0, `ST_DRAIN` 중 약 N cycle 0, `ST_IDLE`에서 `cfg_apply`가 1인 cycle 0.

→ **생산자는 handshake를 완전히 구현한다.** 샘플을 제시한 뒤 수락될 때까지
데이터와 `in_last`를 유지한다. **valid-only 공급은 쓰지 않는다.**

`in_last`는 N을 정의하지 않고 검사만 한다. n=511에 정확히 1이어야 하며, 틀리면
`input_frame_error`가 서지만 프레임은 그대로 진행한다.

프레임 **내부**의 `in_valid` 공백은 결과를 바꾸지 않는다
(매 샘플 1 cycle, 매 샘플 7 cycle, 후반 300 cycle 정지 모두 비트정확). 이는
"공백이 무해하다"는 뜻이고 "`in_ready`를 무시해도 된다"는 뜻이 아니다.

### 6.3 프레임 경계

프레임 마지막 샘플 직후 **1 cycle**이라도 `in_valid`를 내리면 코어가 drain에 들어가
**약 N+25 cycle** 입력을 받지 않는다(N=512에서 537, N=1024에서 1,054 cycle 측정).
데이터는 손실되지 않고 결과도 바뀌지 않는다(에러 플래그 0).

MFCC 예산(hop 160 @ 16 kHz = 10 ms = 1,000,000 cycle @ 100 MHz)에서 537 cycle = 5.4 µs는
무시할 수 있다. **따라서 프레임을 gapless로 붙일 필요가 없다.**
`MFCC_SPEC.md:158`의 "원본의 valid gap을 프레임 경계로 사용하지 않는다"와 모순되지 않는다 —
프레임 경계는 `in_last`와 샘플 수로 정의하고, gap은 성능 요소일 뿐이다.

### 6.4 출력 수용 조건

**기본: 최종 `out_valid`를 매 cycle 받는다.** PL 안에서 power→Mel 누산으로 직결하면 충족된다.

소비자가 멈출 수 있다면 **출력 FIFO + 프레임 수락 제한**이 필요하다. 조건:

| # | 조건 |
|---|---|
| 1 | 프레임 출력은 **N beat 연속·정지 불가 버스트**다. FIFO 깊이 ≥ **N complex word**(N=512 → 32비트 기준 2 KB) |
| 2 | **프레임 첫 샘플 수락 전에** N word 여유가 보장되어야 한다. 프레임 도중 멈춰도 버스트 길이는 줄지 않으므로 게이팅은 **프레임 경계에서** 한다 |
| 3 | 순간 free space만 보면 안 된다. `reserved = N × (수락됐으나 출력 버스트 미완료 프레임 수)`를 유지하고 **`DEPTH − occupancy − reserved ≥ N`**일 때만 새 프레임을 시작한다. **[3판 정정 C9]** 기록된 연속 실행에서 그런 프레임은 **최대 4개** 겹쳤다(1·2판의 '최대 3개'는 틀렸다) |
| 4 | 예약은 그 프레임의 마지막 beat가 FIFO에 들어간 시점에 해제한다 |
| 5 | 조건이 거짓이면 **새 프레임을 시작하지 않는다**(입력단에서 막는다). FIFO가 조용히 넘치게 두지 않는다 |
| 6 | 깊이 N이면 매 프레임 drain 비용(약 N+25 cycle)이 붙는다. **[3판 정정 C9] 연속 1 sample/clock에 '약 3N이면 충분하다'는 1·2판 주장을 철회한다.** 깊이와 credit 방식은 겹침 수만으로 정해지지 않으므로 후속 protocol 설계에서 확정·검증한다 |
| 7 | FIFO write-while-full을 **별도 에러 플래그**로 뽑는다. `bank_collision_error`는 이 상황을 잡지 못한다(6.5) |

코어를 세우는 대안(전역 stall)은 `fft_r22sdf_core`의 `scheduled_token`·상태기 카운터,
각 `r22sdf_runtime_stage`의 `in_token_valid`·내부 카운터·출력 레지스터,
`r22sdf_runtime_group_quantize`의 4단 파이프라인, 재정렬 버퍼의 쓰기·읽기 카운터를
모두 enable로 묶는 **RTL 수정**이다. 미시도·미검증(C5).

### 6.5 오류 플래그 처리

**손실 경로가 두 개이고 플래그가 서로 다르다. 섞지 말 것.**

| 경로 | 조건 | 플래그 | 비고 |
|---|---|---|---|
| ① 내부 bank 충돌 | 코어가 쓰려는 bank가 full | `bank_collision_error` | 29개 시나리오 전부 0. 같은 크기 연속 8프레임까지 미발생 |
| ② 외부 소비자 정지 | `m_axis_tready=0` 또는 소비자가 `out_valid`를 못 받음 | `backpressure_error_sticky`(bit 5)**만** | **①이 잡지 못한다.** 재정렬 읽기측은 멈출 수 없으므로 외부 정지는 bank 충돌을 유발하지 않는다 |

프레임 상태로 수집할 것:

| 플래그 | 의미 | 처리 |
|---|---|---|
| `out_overflow` | 프레임 단위 overflow (모든 beat에서 1로 재생) | 그 프레임 MFCC 무효 처리 또는 재스케일 후 재계산 |
| `input_frame_error` | `in_last` 위치 오류 | 공급기 버그. 프레임 폐기 |
| `reorder_frame_error` | 코어 ordinal/last 불일치 | 설계 오류. 폐기 |
| `bank_collision_error` | 경로 ① | 폐기 |
| `config_consistency_error` | 코어·재정렬 활성 N 불일치 | 설정 시퀀스 오류 |
| `cfg_error` | 설정 거부(**sticky**) | 다음 성공 설정까지 유지됨을 감안해 판독 |
| FIFO overflow (신규) | 경로 ② | 추가 구현 필요 |

`MFCC_SPEC.md:92`의 "음의 에너지, NaN, Inf는 정상 MFCC로 허용하지 않고 오류 플래그·실패
기록을 남긴다"와 같은 방식으로, 위 플래그도 **프레임별 실패 기록**에 포함할 것을 제안한다.

---

## 7. 지금 반영할 사항 vs 추가 실험 후 결정할 사항

### 7.1 지금 반영 (추가 실험 불필요)

| # | 반영 내용 | 근거 항목 |
|---|---|---|
| 1 | N=512 **S = 9**, 자연 순서 출력, 입출력 signed16 Q1.15, twiddle Q1.15 | A1, A2, 3장 |
| 2 | `P = 2048·\|F\|²` 유지 + 정수 형태 `P = psum/2^19`와 소수 비트 19 명시 | A4, 4장 |
| 3 | `MFCC_SPEC.md:121`, `:139`의 "FFT 검토에서 확정 필요" 문구를 확정 결과로 교체 | A1, A2 |
| 4 | 기존 FFT가 **float IP 비교군의 대체가 아님**을 명시 | A3 |
| 5 | "코어에 하류 정지 포트가 **없다**" + 출력 FIFO 조건 7개(6.4) | A6, 6.4 |
| 6 | AXI 판정 기준을 "정지 중 데이터·valid·last 유지 여부"로 바꾸고 **유지하지 않음**을 명시. 기존 래퍼를 참조 구현으로 쓰지 않음 | A7, A8 |
| 7 | 리셋 후 `cfg_apply` 펄스 필수, `cfg_error` sticky, 입력 handshake는 `in_valid && in_ready` | A9, 6.1, 6.2 |
| 8 | 프레임 경계 공백의 drain 비용(N+25 cycle)과 MFCC 예산상 무해함 | 6.3 |
| 9 | 입력 clamp `[-32767, +32767]` 추가 (`z=u/2`는 유지) | B1, B3 |
| 10 | overflow는 wrap이고 프레임 단위 플래그. 실측 피해 규모 | A5, B2 |
| 11 | 오류 플래그 두 경로 분리(6.5) | 6.5 |
| 12 | `twiddle_1024_w16.mem`을 프로젝트 소스로 추가하고 `$readmem ... read successfully` 확인을 빌드 절차에 포함 | 리뷰 M7 |
| 13 | **검증 도구 버전을 Vivado 2024.2로 기재.** 2020.2 결과는 기존 FFT 프로젝트 버전의 증거로 구분 보존 | 리뷰 1장, 2.4, 2.6 |

### 7.2 추가 실험 후 결정

| # | 결정 항목 | 필요한 실험 | 결정권 |
|---|---|---|---|
| 1 | power/Mel 폭 확정(psum 32 / w Q0.16 / 누산 56 / E 소수 35) | 비트모델로 Python float64 대비 오차 측정 + RTL 자원 확인 | Codex |
| 2 | near-silence 처리 방침(5.3의 1·2·3 중 택일/병용) | 개발 음성으로 프레임 레벨 분포와 `E_int==0` 발생률 측정 | Codex |
| 3 | 저에너지 프레임의 **층화 보고 형식**(전체 통계는 유지) | `FIXED_POINT_PILOT.md` 4장 형식 검토 | Codex |
| 4 | BFP 채택 여부 | 실험 E1~E4(8장) | Codex |
| 5 | 출력 FIFO 깊이와 credit 방식, AXI-Lite/DMA 중 어느 경로 먼저 | **protocol 설계 + 시뮬레이션**(깊이를 수치로 확정하기 전에 필요) | 사용자/Codex |
| 6 | PS7 포함 100 MHz 타이밍 | board preset 확보 후 블록 디자인 구현 | — |
| 7 | pre-emphasis·윈도 중간 폭·소수부·반올림 위치 | 범위·오차 분석 | Codex |

---

## 8. BFP — 후보로 유지, 채택 판단용 후속 실험

### 8.1 현재 상태: **후보. 채택하지 않음**

합성 신호에서 효과는 확인했으나 **실제 음성 기반 근거가 없다.**

| 시험한 조건 | 값 |
|---|---|
| 입력 | `numpy` seed 99 가우시안 잡음 1개를 peak 1로 정규화 → Hamming 윈도 → 목표 peak로 스케일. **실제 음성 아님. pre-emphasis 없음** |
| shift 정책 | `pk << (s+1) <= 16383`을 만족하는 최대 `s` (상향 후 peak < 0.5, 1비트 여유) |
| 기준값 | **같은 양자화 입력**의 float64 `numpy.fft` (입력 양자화 오차 제외) |
| Mel | 내가 작성한 float64 26-band 삼각 필터(16 kHz, fmin 0, fmax 8000, bin edge `floor`, 정규화 없음). **Codex 규격과 비트 단위로 맞추지 않음** |
| 로그 하한 | 비율 안정화용 `1e-30`을 분자·분모에 가산. **규격의 `1e-12` 미적용** |
| 후단 | DCT·lifter·delta **없음** |
| 결과 | 입력 peak 1.0~0.0003에서 최대 \|Δlog\| 0.011~0.058 dB. seed 5의 400 프레임에서 최악 band 0.046~0.181 dB, overflow 0 |
| 비교 | 같은 신호에서 BFP 없이 고정 α=1/2만 쓰면 peak 0.01에서 1.19 dB, 0.003에서 2.57 dB(13/26 band가 1 dB 초과) |

→ "합성 잡음에서는 BFP가 조용한 프레임의 Mel 로그 오차를 0.2 dB 이내로 유지했다"까지만
말할 수 있다. **MFCC 정확도 개선의 증거는 아니다.**

### 8.2 채택 판단용 후속 실험 (개발 음성 기반)

전제: `MFCC_SPEC.md:153`의 중간 단계 저장(`P[257]`, `E[26]`, `lnE[26]`, `C[13]`)과
Codex의 Python 기준 경로가 먼저 있어야 한다.

| # | 실험 | 입력 | 측정 | 판단 기준(제안) |
|---|---|---|---|---|
| **E1** | 프레임 레벨 분포 조사 | 개발 음성 전체. pre-emphasis + Hamming 후 프레임별 peak와 `α=1/2` 적용 시 `E_int==0` band 수 | peak의 히스토그램, `E_int==0` 발생 프레임 비율, band별 발생률 | 발생률이 무시할 수준이면 BFP 불필요 → 7.2-2의 방침 1·2로 충분 |
| **E2** | 고정 α vs BFP의 MFCC 오차 | 같은 음성, 같은 프레임 경계. (a) α=1/2 고정, (b) BFP(shift 정책은 8.1과 동일) | `C0…C12`의 프레임별 최대 절대 오차·RMSE를 Python float64 기준 대비. `MFCC_SPEC.md:168-173`의 비교 절차 사용 | BFP가 (a) 대비 오차를 유의하게 줄이고, 그 개선이 저에너지 프레임에 집중되는지 |
| **E3** | **[3판 정정 C14] 삭제.** 저음량 프레임 제외/임계값 제안은 철회했다. 층화 분석은 `FIXED_POINT_PILOT.md` 4장에서 수행했고 전체 통계를 대체하지 않는다 | — | — | — |
| **E4** | BFP의 부작용 | E2의 BFP 경로 | 프레임마다 달라지는 shift가 (i) 프레임 간 `C0` 연속성, (ii) delta/delta-delta, (iii) CMVN에 주는 영향. 지수 전달 경로의 RTL 비용(peak 검출 + 배럴 시프트 + 지수 레지스터) | 부작용이 E2의 이득보다 작아야 채택 |

실험 설계 시 주의:

- shift는 프레임별로 달라지므로 **지수를 프레임 메타데이터로 반드시 함께 저장**해야 한다.
  저장하지 않으면 PC에서 복원 불가다(`MFCC_SPEC.md:153`의 `fractional_bits/scale`에 추가).
- `E_int` 비교는 shift 보정 **후**, 로그 하한 적용 **전**의 정규화 E에서 해야 한다
  (`MFCC_SPEC.md:133`의 "floor 전의 E를 기준으로 적용"과 동일 취지).
- BFP를 쓰더라도 상향 후 peak가 full scale에 닿지 않게 **1비트 여유**를 둔다(8.1의 정책).
  full scale까지 올리면 리뷰 5.3의 오버플로 영역에 들어간다.
- BFP는 **FFT RTL을 수정하지 않는다.** 입력단 스케일러와 출력단 지수 보정만 추가된다.

---

## 9. 근거 로그 색인

| 주장 | 로그 / 스크립트 | 상태 |
|---|---|---|
| 29개 시나리오 비트정확, 플래그 0 (2024.2) | `claude-review-2024_2/logs/check_probe_2024_2.txt`, `check_probe2_2024_2.txt` (종료 코드 0) | 재현 가능 |
| 2024.2 시뮬레이션 종료 코드 전부 0 | `claude-review-2024_2/logs/run_sim_2024_2_console.log`, `RESULTS_2024_2.txt` | 재현 가능 |
| 2020.2 ↔ 2024.2 출력 바이트 동일 | `RESULTS_2024_2.txt` (SHA-256 비교) | 재현 가능 |
| 지향 검사 28/28 | `claude-review-2024_2/logs/directed_xsim.log` | 재현 가능 |
| 100 MHz OOC post-route (2024.2 WNS +0.106 ns, LUT 5138 / FF 1304 / BRAM 3 / DSP 20) | `claude-review-2024_2/work/synth_fft_stream_top_10.000/post_route_*.rpt`, `logs/ooc_*.log` | 재현 가능 |
| 소스·ROM 해시 원본 일치 | `claude-review-2024_2/logs/source_hashes.txt` | 재현 가능 |
| checker 검출력(음성 대조) | `claude-review/logs/check_probe_fixed_negative_controls.txt` (종료 코드 1) | 재현 가능 |
| ROM 1,024 entry 독립 재계산 일치 | `claude-review/logs/` + `validate_model.py` | 재현 가능 |
| 오버플로: full scale 발생 / peak 0.975 0건 / `-32767` clamp가 제거 | `claude-review/logs/sweep_clamp_check.txt`, `sweep_clamp_check.py` (seed 20261004, 13,168 프레임) | 재현 가능 |
| 출력 잡음 바닥 ~0.6 LSB, 레벨별 NMSE | `claude-review/logs/sweep_range.txt`, `sweep_range.py` | 재현 가능 |
| Mel 로그 오차(합성 신호), BFP 효과 | `claude-review/logs/sweep_headroom.txt`, `sweep_headroom.py` | 재현 가능 |
| power/Mel 폭, `1e-12` 위치, 0인 bin 수 | `claude-review/logs/power_mel_widths.txt` | **재현 자료 부족** — 저장되지 않은 인라인 명령으로 생성. 같은 내용은 `verification/fixed/test_power_mel.py`와 `test_coeffs.py`로 대체 검증했다(스크립트 보존됨) |
| peak 0.70~0.99 전 구간, pre-emphasis+Hamming 1,000프레임, 복소 한계 | `claude-review/logs/sweep_headroom_band.txt` | **재현 자료 부족 — 근거로 쓰지 말 것** |

마지막 행에 주의. 해당 로그는 저장되지 않은 인라인 명령으로 생성되어 스크립트가 없다.
결정적 결론 세 개만 `sweep_clamp_check.py`로 축소 재현했다
(결정론적 사각파 계수 19/256·32/512·60/1024가 원래 로그와 일치해 재구성의 교차 확인은 됨).
