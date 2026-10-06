# 부동소수점 IP MFCC 하드웨어 구현·검증

작성: 2026-10-04 KST. 단일 연속 검증 갱신: 2026-10-05 KST. 대상 `xc7z020clg400-1`, Vivado 2024.2 SW5239630. 사용자의 우선순위 변경에 따라 ARM 보드 실행을 보류하고 부동소수점 IP MFCC와 Verilog/SystemVerilog BRAM 버퍼를 작성했다. 기존 ARM 플랫폼/ELF, C·Python 기준값, Claude 담당 고정소수점 코드·FFT 검토와 원본은 수정하지 않는다.

이 문서는 새 실행의 근거를 기록한다. 기존 C는 실제 음성 평가를 통과했으나 `fullscale_alternating`, `tone_bin32_1000hz`에서 정확도 목표에 미달한 상태다. 그 사실을 유지하며 하드웨어 검사의 자동 면제 사유로 쓰지 않는다. 최종 FP32 하드웨어도 모든 입력의 정확도 검사를 통과한 골든 모델이 아니다.

| 완료 항목 | 직접 검증한 결과 |
|---|---|
| PCM16→raw13 전체 RTL/IP 경로 | 구현·실제 vendor simulation 완료 |
| BRAM 프레이머 |13개 clip 및 단일85920word/534frame 시험 통과;1RAMB18 |
| 최종 합성 입력17개 |40frames 프로토콜 통과;12cases 전 단계 통과/5cases 수치 실패 |
| 개발 음성1개: 단일 연속 실행 |85920 PCM samples를 start/end/done 각1회로 처리;534frames/6942MFCCs,10단계 Python/C 비교·프로토콜 통과 |
| 기존 구간 재생과의 비교 |보존한534frame 결과와10단계/1172022개 binary32 성분 전부 bit 일치 |
| 개발 MFCC 오차(HW−Python) |최대 `2.70097533111e-4`,RMSE `2.79105453392e-5` |
| Zynq7020 OOC 배치배선 |LUT4379,FF7801,16RAMB18,DSP26;10ns 내부 WNS+0.942ns |
| 실제 보드·평가 음성20개·전력/처리 시간 |이번 FP32 작업에서 실행하지 않음 |

전체 음성은 기존8개 구간 재생에 더해 **실제 vendor IP로 단일 연속 MFCC 클립 검증을 완료**했다. 이 시뮬레이션은 입력 gap과 backpressure·출력 stall을 포함하므로 보드 처리 시간이나 무정지 입력 처리율 측정으로 확대하지 않는다. 합성 입력5개 실패와 보드 미검증 상태는 아래에 그대로 남긴다.

## 1. 원본에서 확인한 연결과 변경 근거

원본 경로: `D:\2610_MFCC\reference_code\github_mfcc\FPGA source\Calculation MFCC features`. 출처는 [AlexKly GitHub](https://github.com/AlexKly/Simple-Voice-Activity-Detector-using-MFCC-based-on-FPGA-Kintex), 보존 commit `27aa09974049d11f49383c8e18f3ca9e34d08ed9`. 전체 출처·해시는 `reference_code/SOURCE_MANIFEST.json`, 기존 세부 조사는 `docs/SOURCE_AUDIT.md`에 있다. 이번에 원본의 다음 내용을 직접 재확인했다.

| 원본 | 소스에서 확인한 사실 | 이번 설계 판단 |
|---|---|---|
| `MFCC.v` | pre-emphasis→get_frames→windowing→power→filterbanks→log→DCT→scale 뒤 lifter/energy replacement/delta 연결. `bclk/g_clk` 두 도메인, framing/window/reset 상수 연결 | 본 비교 raw13만 연결. 단일 clock, explicit reset/clip events, 후처리 제외 |
| `get_frames.vhd` | 512개 입력/출력 배열을 shift·snapshot; slow/fast clock 사이 raw arrays와 `en_send`, `flag_first_enterance` 공유. reset이 일부 배열만 다룸 | 언어 때문이 아니라 CDC·reset·backpressure 계약 때문에 새 SV BRAM framer 작성 |
| `Pre_emphasis.v` | 이전 샘플이 매 clock 갱신되며 입력 valid와 독립. FP operand TREADY 미사용 | accepted PCM마다 한 번, clip 시작에서만 prev=0; blocking IP 각 operand 독립 수락 |
| `power_spectrum.v` | FFT input TLAST wire 미구동, ready 무시, output gap에서257-bin counter reset. re²+im² 뒤 sqrt와 재제곱·512나눗셈 | forward config handshake, 정확히512입출력·index/TLAST 검사,257bin에 직접(re²+im²)/512 |
| `filterbanks.v` | 26개 병렬 multiplier/adder, 매 sample feedback, negedge-valid로 결과 latch | 공유 ALU와 BRAM. 각 덧셈 결과를 기다린 후 다음 항 누산; clock enable/상태만 사용 |
| `dct_type2.v` | 13개 병렬 multiplier/adder 및 negedge-enable 처리 |26항 cosine dot 후 scale 한 번. C0…C12 순차 출력 |

원본 GitHub HDL을 이식·컴파일해 성공했다고 주장하지 않는다. 새 제어기는 공통 수학과 현재 IP 인터페이스에 맞춰 작성했다. 원본의 모든 하위 모듈 수치가 잘못됐다는 주장도 아니다. 원본 license는 기존 audit에서 미확인이며, 원본 RTL/계수/DCP를 새 합성 fileset에 복사하지 않았다. Vendor IP와 XPM은 설치된 AMD 라이선스·고지를 보존한다.

이번 읽기에서 확인한 SHA256:

| 파일 | SHA256 |
|---|---|
| MFCC.v | `d00222e67bcdc9506e50c3bff1171218b20ccc8caa277fa9ae83b4c45d2c4ccc` |
| get_frames.vhd | `9b439d5fe2bbc817cbae908952e9cd40a6e4699b854f6e0ac9081c02e89b9b2c` |
| Pre_emphasis.v | `4d67c3c6fcaa12a44a8640803ebd8c59638ed6fc82fabf93baba4d8f8cda93a8` |
| power_spectrum.v | `7495f38eab9a8bcbc8844bda832100afc1294e9d5f4f903d7197301418da8c52` |
| filterbanks.v | `1629dd56d0e76286f31a55e81773c42f0cd2efba88e4f43b1b9f1bec6fad0f1c` |
| dct_type2.v | `aff3c6b0e8b88a4026ae4cbe68ec4070eac60d4c3f299bd871f3c3dca4c5b4f0` |

## 2. 구현 계약과 실제 IP

구현 파일은 `hardware/fp32/rtl/`의 `fp32_mfcc`, `fp32_preemphasis`, `fp32_frame_buffer`, `fp32_window`, `fp32_backend`, `fp32_alu`다. `fp32_backend.sv`에는 공용 XPM storage wrapper도 포함된다. 합성 대상은 신규 SV, vendor IP/XPM이며 testbench의 real/DFT/file I/O는 합성하지 않는다.

**설계 제안/구현:** 입력은 preframed float가 아닌 동일 PCM16이다. `/32768`→연속 pre-emphasis→512/160 full framing→symmetric Hamming→FFT512→power257→Mel26→floor/ln→orthonormal DCT raw13 전체를 RTL/IP가 수행한다. EOF는 마지막 PCM handshake 이후 전송하며, pending pre-emphasis·마지막 frame·C12까지 drain한다. input gap/stall은 previous sample이나 counter를 초기화하지 않는다. 완료/출력은 valid/ready로 보존한다. 프로토콜과 reset 최소2clock, 오류 복구는 `hardware/fp32/README.md`에 명시했다.

BRAM 구조는 코드 전에 [FP32_BUFFER_ARCHITECTURE.md](FP32_BUFFER_ARCHITECTURE.md)에 작성했다. 이번 파일 입력은 정지 가능하므로512word ring 한 개로 충분하다. frame read 동안 쓰기를 막고352sample overlap을 유지하며160sample만 교체한다. 전체 RAM reset/복사 `_next` 배열, valid를 clock로 사용, 사용자 `for/function/task/initial`, runtime real 수학은 새 합성 RTL에 없다. Ping-pong은 추가하지 않았으며, 끊을 수 없는 실시간 입력에는 별도 처리율·버퍼 예산이 필요하다.

**소스·도구에서 확인:** 최종 `build/fp32_hw/ip_04`에 새 Floating Point7.1 Rev19와 FFT9.1 Rev13를 생성했다. `ip_03` 대비 변경은 5개 IP의 native ACLKEN 사용뿐이며, parameter/port diff로 수치 설정·latency·폭의 동일성을 확인했다. 이전 Kintex DCP는 사용하지 않는다. [IP_CONTRACT.md](../hardware/fp32/ip/IP_CONTRACT.md)에 실제 포트·latency·II·blocking·reset·license 상태와 근거를 기록했다. PCM converter는 signed W16/F15(`C_A_Exponent_Width=1`,fraction15), mul9/addsub12/log23/converter7 pipeline clocks다. Blocking stall 포함 외부 지연은 handshake로 판정한다. Add/Sub opcode00/01, 각 operand 수락은 따로 기록한다.

사용하지 않는 연산 IP는 vendor `aclken`으로 정지한다. clock 자체를 게이팅하지 않는다. ALU ISSUE/WAIT의 해당 연산, FFT CONFIG/FEED/FFT_READ, PCM converter SEND/WAIT에서 enable한다. reset과 해제 후 첫1clock은 모든 IP를 enable하며 신규 거래를 막는다. enable=0일 때 valid/ready 수락이 생기지 않도록 제어한다. PG060/PG109의 state-hold/reset 계약을 근거로 구현했고 실제 IP 시험을 반복했다. 전력 절감이나 시뮬레이션 속도 개선은 측정된 결과로 주장하지 않는다.

FFT는 radix2 burst, nonrealtime, natural order, input/output float32, forward config`24'h000001`다. [PG109](https://docs.amd.com/r/en-US/pg109-xfft/Floating-Point-Considerations)에 따른 내부 정규화·고정 연산을 갖는 floating-interface FFT다. 전 butterfly가 IEEE float32라고 쓰지 않는다. Float mode에서 Vivado가 비활성 `unscaled/convergent_rounding` 요청을 무시한 사실도 기록했다. 실제 이득은 F1으로 확인하며 입력 크기에 따라 임의 보정하지 않는다.

계수는 고정된 C `reproduce_01` binary32를 검증·bit-preserving ROM export한다. 원래 binary64→binary32 변환 근거는 C coefficient manifest로 연결된다. ROM export script는 shape, 원본 SHA256, 상수 `.95=3f733333`, `floor=2b8cbccc`, 전체 roundtrip mismatch0을 확인한다. Mel은 ascending bins의459개 nonzero 항, DCT는 ascending26항 cosine+scale이다. FP multiply/add는 개별 반올림하며 FMA를 쓰지 않는다. PG060의 round-nearest-even/subnormal-to-zero 차이는 PC의 모든 IEEE 특수값과 같은 것으로 간주하지 않는다.

최종 저장 방식은 Mel459words를512word BRAM에,26개 행별 first-bin/last-bin/offset을32word BRAM에 둔다. 기존 dense6682word `mel.mem`도 해시가 같은 감사용 파일로 생성한다. `compact_coeff_probe_01`에서 모든6682word의 복원 bit mismatch0과459 MAC 단어·순서의 mismatch0을 확인했다. 기존에0word를 읽던6223회 순회만 제거하고 모든 FP 연산을 유지했다. descriptor 주소 범위 오류는 backend error11이다. 이 변경의 새 실제 IP smoke·합성·전체 비교를 수행하며, 아래 dense-CE 실행의 결과와 구분한다.

## 3. 검증 기준과 범위

모든 tolerance는 실행 전에 기존 `verification/c/tolerances.json`을 snapshot한다. SHA256 `64ad3ab3a0ae050562b9ccfab7b3c8e7fcc90eb527eb6811cfcbbd4c07a9d2b6`. per-element `abs(HW−Python) <= atol + rtol*abs(Python)`, complex FFT는 complex magnitude다.

| 단계 | atol | rtol |
|---|---:|---:|
| PCM→float |0|0|
| pre-emphasis,frames,windowed |2e-7|2e-6|
| unnormalized complexFFT |2e-5|2e-5|
| power,Mel |1e-8|5e-5|
| log-Mel,DCT,MFCC |1e-3|1e-5|

작은 에너지의 절대 오차 통과만으로 로그/MFCC 성공을 추정하지 않는다. 실패하면 최초 실패 단계와 downstream error를 그대로 남긴다. 입력·로그 하한·공통 수식·Python 기준값·허용치를 결과에 맞춰 바꾸지 않는다. PC C와의 오차와 binary32 bit 일치는 보조 지표이며 Python tolerance를 대체하지 않는다.

검증은 실제 XPM/FP/FFT vendor behavioral simulation이다. `bmstub`, software MFCC 대체, RTL real 연산으로 결과를 대신하지 않는다. 입력 PCM은 Python snapshot과 hash·hex roundtrip 동일성을 확인한다. synthetic17+development1만 선택하며 평가20은 실행하지 않는다. 개발 음성은 `8463-294828-0037`,85920samples,534frames이며 PCM SHA256 `026cae2934935a27911f88d7d6e0ddcc678ad433fb50fe764233a57e4bbcde32`다.

### F0: BRAM buffer

`build/fp32_hw/f0_verified_01`에서 실제 XPM xsim 검증 및 OOC synthesis를 완료했다.13개 완료 clip,21521개 checked output word(중단 prefix 포함),최대4512samples로 다중 ring wrap, 입력 공백/연속 valid, 긴 출력·마지막 beat stall, read response 후 stall, empty/short/boundary lengths, reset-mid-operation, multi-clip, EOF-during-last-stall, done hold를 확인했다. input stall81445cycles, output stall14596cycles, last stall4090cycles, done hold213cycles. 기능 통과와 별도로 합성에서 **1RAMB18,0RAMB36,135FF,177LUT,0latch**를 확인했다. 전체 MFCC 자원이나 timing 수치가 아니다.

`f0_long_clip_01`에서는 같은 RTL을 실제 XPM으로 한 클립85920words 연속 처리해534frames/273408출력words를 전수 검사했다. 입력은 식별 가능한 결정적32-bit payload이며 음성 연산 시험이 아니다. 모든 출력의 sample identity, 전체32-bit frame ID/start, 마지막frame533/start85280, tail128폐기,167write wrap/534read wrap을 확인했다. 입력 gap7855회, input stall1280835cycles, output stall187260cycles, last-beat stall52067cycles, done hold17cycles와 완료 후64clocks의 stale output 없음도 통과했다. 산출물52개 해시가 일치했다. 별도 MFCC 수치 시험의 구간 재생과 독립적으로 프레이머의 전체 길이 연속 상태를 검증한 결과다.

### F1: FFT

`build/fp32_hw/fft_unit_probe_01`의 실제 IP 실행에서5cases×512=2560complex outputs가 고정 FFT 허용치를 통과했다. impulse0의 모든 bin=1, DC1의 bin0=512+j0, sin32의 bin32=−256j로 unnormalized gain1과 forward 부호를 확인했다. XK_INDEX0…511, TLAST511, finite,1309output stallcycles 포함 검사를 통과했다. worst complex error `1.29880956346e-5`, 전체RMSE `5.27860747795e-7`. oracle은 테스트벤치 입력binary32에 대한 독립 float64 직접DFT이며 **하드웨어 FFT 자체는 실제 O(N log N) Xilinx IP**다.

최종 CE 설정 `fft_unit_ce_02`에서는 동일2560bin이 기존 IP03과 bit mismatch0으로 일치했다. config19/input155/compute265/held-output115/output수락직후185, 총739clock 동안 CE 정지·복구를 확인했다. 일반 output stall1366cycles, 순서·개수·TLAST·finite와 고정 FFT 허용치 모두 통과했다. 산출물255개 hash 재검사도 일치했다.

### F1: 공유 ALU

`alu_unit_ce_01`에서 실제 vendor IP로11requests/10responses와 reset-aborted1건을 확인했다. MUL/ADD/SUB/nearest-even tie/취소·signed zero/LN1/LNfloor 등10수치 시험, 최대53clock 결과 hold,8clock reset 후 복구가 통과했다. CE=0에서 MUL398/ADD366/LOG384edge의 출력 유지와 disabled handshake 없음도 확인했다.10개 결과는 CE 이전 `alu_unit_03`과 비트가 동일하다.

`ln(float32(1e-12))`의 vendor 결과는 `c1dd0c54`, 올바르게 반올림한 double-log 기준은 `c1dd0c55`로1ULP, 절대오차 `1.5276391955865165e-6`다. 기존 log 허용치 이내지만 bit-exact라고 쓰지 않는다. 실제 IP에서 A/B가 서로 다른 clock에 자연 수락되는 경우는 관측0회다. wrapper는 독립 accepted flags를 구현·검토했지만 그 동적 비동시 수락 coverage는 **미확인**이다. 강제 신호 masking을 시도한 `alu_unit_02` timeout은 실패 이력으로 보존했다.

### F2: PCM부터 최종 MFCC

CE 이전 `integration_smoke_02`에서 실제 전체 경로의832samples/3frames/39coeffs와 모든 protocol 검사가 통과했다. 결과 JSON 기록의 NumPy 정수 직렬화 오류는 `smoke_02_analysis`라는 새 폴더에서 원시 trace를 보존한 채 수정된 비교기로 재분석했다. 모든 단계가 통과했고 MFCC 최대오차 `3.174020107285111e-6`. 재분석의 별도 manifest에 원시 trace·기존 freeze·실제 비교 소스 hash를 연결했다.

최종 CE 회로의 `integration_ce_smoke_01`은 exit0으로 완료했다. 동일 임펄스832samples/3frames/39coeffs 전체 경로와10개 단계 모두 허용치 이내이며, 각 단계 binary32 배열은 CE 이전 결과와 바이트 단위로 동일하다. 중간 reset 스트레스도 **5개 abort 지점 + 정상 baseline1frame + replay5frames**가 통과했다. 지점은 PCM 변환 입력 수락, BRAM read index17, FFT 마지막 입력 수락, 로그 operand 수락(FFT CE정지 중), C12 출력 stall이다.16clock reset 뒤 모든 replay의13개 결과·메타데이터·done drain이 baseline과 bit-exact였고, stale/extra output은 없었다. 이 시험은16clock reset 동작을 검증하며 모든 상황에서 최소2clock reset을 동적 검증한 것은 아니다.

Dense-CE의 합성17개 실행은 `integration_ce_synthetic_01`에 보존한다. 이전 backend의 전체 음성 실행 `integration_ce_development_01`은 compact smoke와 전체 길이 F0 시험을 확인한 뒤18:27KST에 의도적으로 중단했다. 정확한 소유 프로세스 경로·명령을 재확인해 해당 xsimk 하나만 중단했으며, freeze와 원시 trace는 변경하지 않았다. 실행기는 전체 summary가 없어 `failed`로 종료했다. 중단 사유·PID·해시는 `dense_development_interruption_01/interruption.json`, 완료된 **46/534frames**의 별도 부분 진단은 `dense_development_final_partial_01`에 있다. 이 prefix의8개 프레임 단계에서는 순서·finite·허용치 위반이 관찰되지 않았지만 **전체534frames 통과가 아니다**. 더 이른6frame 진단 `partial_development_02`도 덮어쓰지 않았다.

`integration_ce_synthetic_01`은18:32:53KST에 전체17cases/40frames의 프로토콜 검사를 완료했다. 수치는12cases 전 단계 통과/5cases 실패이며 exit2다. 아래는 **Mel 압축 전 회로의 실제 완료 결과**이고 최종 compact 회로의 결과와 별도 대조한다. 더 이른26/40frame 진단 `partial_synthetic_01`도 보존했다.

| 합성 입력 | 최초 허용치 초과 단계 | log-Mel 최대오차 | MFCC 최대오차 | MFCC 위반/39 |
|---|---|---:|---:|---:|
| composite_500_2237hz |log-Mel|0.00123342105|0.000925610278|0|
| dc_negative_8192 |log-Mel|0.0201063658|0.0105168633|22|
| dc_positive_8192 |log-Mel|0.0201234316|0.0113117881|22|
| fullscale_alternating |FFT|1.60760841146|2.72703754503|27|
| tone_bin32_1000hz |log-Mel|0.0529445958|0.0596997040|24|

첫3개는 기존 PC C에서 통과한 입력이다. 따라서 C의 알려진 두 실패와 새 IP 하드웨어 실패를 동일하게 묶지 않는다. `composite`는 최종MFCC만 통과하지만 중간단계 log 실패 때문에 전 단계 판정에서는 실패다. 나머지12cases에는 출력0개인 짧은 입력2개가 포함된다. NaN/Inf·frame 누락·중복·순서 오류와 수치 오차를 별도로 검사했다.

`dense_synthetic_diagnosis_01`은 완료된 실제 배열의 해시·형식·원본 계수 연결을 확인한 후, 기록된 operand에 float64 식을 적용한 **진단 전용 재계산**이다. DUT나 기준 정답을 대체하지 않는다. `fullscale_alternating` frame1/filter9의 Mel은 Python `5.93883067206e-13`, HW `4.99085234945e-12`였다. 고정 floor1e-12 주변의 이 차이가 ln에서 약1.6076067이 되며, 같은 HW operand에 대한 vendor ln 잔차는 약1.70e-6이다. 전체 실패5cases의 vendor ln 잔차 최대치는1.91e-6 미만이었다. 따라서 큰 로그 오차의 주된 항은 ln IP 자체의 근사 오차가 아니라 앞 단계에서 이미 달라진 에너지다.

실제 HW window에 float64 FFT를 적용한 값과 기록된 FFT 사이의 최대 complex 잔차는 fullscale에서3.6905687e-5였다. 같은 HW window 이후 전체를 float64로 재계산해도 MFCC 차이가0.04643277 남았으므로 FFT 하나만의 문제라고 단정하지 않는다. Frontend/window binary32 반올림과 vendor FFT의 수치 차이를 함께 구분해야 한다. 이 진단으로 floor·입력·Python 정답·허용치를 바꾸지 않았다. 에너지/로그 분해 그림과 각 단계 잔차는 해당 `diagnosis.json`과 `*_energy_diagnosis.png`에 보존했다. 그림의 허용선은 실제 HW−Python 합계 오차에 적용되며 개별 진단 성분의 합격선이 아니다.

Compact 전체 경로의 `integration_compact_smoke_01`은 임펄스832samples/3frames/39coeffs와10단계 검사를 통과했다. `compact_smoke_equivalence_03`에서 저장된10개 단계 파일의7283개 binary32 component word가 dense-CE smoke와 비트 동일함을 확인했다. 비교기는 완료 상태·원본 PCM·profile·허용치·reference schema와 artifact hash를 먼저 확인했다. 입력 공백·출력 stall을 포함한 이 clip의173249cycles는 dense의247679cycles보다74430 적다. LFSR 기반 공백/stall 위상이 회로 지연에 따라 달라지므로 단순한3×24814와 같다고 쓰지 않으며 보드 처리 시간으로 환산하지 않는다.

최종 회로 `integration_compact_synthetic_01`의 reset stress는5개 abort 지점, baseline1/replay5frame,16clock reset,584517cycles로 완료했다. 모든 replay는 baseline과13계수·메타데이터가 bit 일치하며 stale/extra output이 없었다. 정상 실행도19:58KST에 실제 Vivado simulation을 끝내고 **17cases/40frames의 프로토콜 통과,12cases 전 단계 통과/5cases 수치 실패**로 완료했다. 실행 상태는 `completed_with_numerical_failures`다. 위 표의4개 MFCC 실패와1개 log-Mel-only 실패가 최종 compact에서도 그대로 발생했다.

`compact_synthetic_equivalence_01/equivalence.json`은 dense-CE와 최종 compact의 **17cases×10단계=170개 파일이 모두 bit 동일**함을 확인했다. 계수·입력·reference·완료 상태·artifact provenance를 먼저 검사했으며, 메모리 압축에 따른 추가 수치 오차는 이 입력 집합에서0이다. 최종 실행을 직접 대상으로 한 `compact_synthetic_diagnosis_01/diagnosis.json`과5개 진단 그림에서도 위 에너지/로그 잔차 결과를 다시 확인했다. 진단은 원시 HW 결과나 허용치를 바꾸지 않는다.

개발 음성의 `segmented_development_01`은20:10:30KST에 **`completed_segmented_passed`/exit0**으로 완료했다.8개 실제 vendor simulator 모두 exit0이었다. 복사한 simulator 실행파일/라이브러리/ROM/ini는 완료한 smoke의 artifact index와 모든 해시를 대조했고 실행 뒤에도 불변성을 확인했다. `segmented_probe_02`에서 복사 후 실제1frame의10단계·프로토콜·실행 후 불변 파일 해시 검사가 통과했다. 첫 probe의 Windows batch `=` 인자 인용 오류는 `segmented_probe_01`에 실패 이력으로 보존했다.

CE 이전의 전체 합성17개 실행 `integration_synthetic_02`도 완료했다. `ce_synthetic_equivalence_01`은 그 결과와 dense-CE의 모든17case/170개 단계 파일이 bit 일치함을 확인했다. 이는 native CE 변경이 이 입력 집합의 저장된 수치 결과를 바꾸지 않았다는 증거이며, 전력·성능이나 임의 입력에서의 형식적 동등성 주장은 아니다.

구간 재생은 합성 수식·정답을 바꾸지 않는다. 각 소유 프레임 구간[a,b]에서 a>0이면 시작 샘플을160(a−1)로 두어 첫1frame을 준비용으로 계산한다. 첫 local sample만 prev=0의 영향을 받으므로, 수치 집계는 localframe1부터 수행한다. 이 준비 frame도 순서·finite·개수·오류 검사는 모두 받는다. 마지막 구간은 원본85920samples까지 읽어128sample tail을 유지한다. 각 앞 구간은 다음 구간의 첫 유효 frame을 추가 계산하여8개 프레임 단계의 비트가 같은지 확인한다.

실제 총548localframes/90624accepted samples/7124coefficients에서 준비7frames와 경계 중복7frames를 구분하여 원본534frames/6942MFCCs와85920개 sample을 각각 정확히 한 번 집계했다. 소유권 검사 min=max=1로 누락·중복0이었다. 모든 준비 frame도 raw protocol·finite 검사를 받았다. 경계 globalframe66,133,200,267,333,400,467의8개 프레임 단계가 모두 bit 일치했으며, 입력 overlap4704개와 pre-emphasis overlap4697개도 bit 일치했다. 입력 소유 구간을 합친 PCM은 원본 바이트와 완전히 동일하다.

원래 전체 Python/C 배열의 같은 globalframe과 비교했으며 **10개 단계 모두 두 비교에서 허용치 위반0·NaN/Inf0**이었다. 입력·pre-emphasis·frame·window는 PC C와 모두 bit 동일했다. FFT 이후는 허용치 내에서 다르며 최종 MFCC의 bit 일치는240/6942개다. 따라서 전체 HW와 PC C가 bit-exact라고 주장하지 않는다.

| 단계 | HW−Python 최대 절대오차 | HW−Python RMSE | HW−PC C 최대 절대오차 | HW−PC C RMSE |
|---|---:|---:|---:|---:|
| input_float |0|0|0|0|
| preemphasis |3.57627868e-8|2.43766390e-9|0|0|
| frames |3.57627868e-8|2.44523729e-9|0|0|
| windowed |3.97260865e-8|1.82454056e-9|0|0|
| complex FFT |2.34836668e-6|1.38512622e-7|2.31197204e-6|1.45215349e-7|
| power |7.53639202e-8|8.58484097e-10|7.45058060e-8|8.13017719e-10|
| Mel energy |1.71083146e-7|4.50787871e-9|1.19209290e-7|3.08754636e-9|
| log-Mel |5.44364056e-4|2.13173351e-5|5.07354736e-4|2.11505403e-5|
| DCT |2.70097533e-4|2.79105453e-5|2.70843506e-4|2.76536544e-5|
| MFCC C0…C12 |2.70097533e-4|2.79105453e-5|2.70843506e-4|2.76536544e-5|

최대 MFCC 오차는 globalframe489/C1에서 발생했다. HW `-24.955432891845703`, Python `-24.955702989378814`, 해당 요소의 허용폭 `0.001249557029893788`이었다. 이번 고정 목표를 통과했다는 뜻이며, 인식 정확도나 임의 음성의 오차 상한을 의미하지 않는다.

결과는 `segmented_development_01/comparison.json`, 단계별 binary32 배열은 `arrays/8463-294828-0037/`에 있다. 배열의 행은 검사 후 원본 globalframe 순서로 재배열한 것이며, 원래 local ID/start와 cycle은 worker raw trace에 남는다. 전체619artifact files(365900129bytes)를 독립 재hash해 불일치0,10배열의 shape/byte 길이/hash 일치를 확인했다. artifact index SHA256은 `8af76152e3b00efd204be083d9c6bc59287253ffaae1fb46b970949b63985b7c`다.

대표 그림은 같은 run의 `figures/8463-294828-0037_segmented_replay_heatmaps.png`와 `figures/8463-294828-0037_segmented_replay_coefficient_errors.png`다. 전자는 Python/PC C/HW와3가지 차이, 후자는 계수별 최대 절대오차/RMSE를 보여준다. 이미지를 열어 라벨·색상 범위·534frame 범위를 확인했다. 차이를 보여주는3개 패널은 개별 색상 범위이므로 색의 진하기만으로 오차 크기를 비교하지 않는다.

**위534프레임의 분할 수치 검증은 아래 단일 클립 전체 MFCC의 연속 프로토콜 검증과 별도 실행이다.** 기존 구간 실행의 `single_clip_protocol_passed=null`, `single_clip_throughput_measured=false`와 artifact index는 그대로 유지한다. 원시 local cycle을 합산하여 board 시간이나 단일 clip 성능으로 환산하지 않는다.

### F2: 최종 compact 개발 음성의 단일 연속 클립

`D:\2610_MFCC\build\fp32_hw\continuous_development_01`에서 개발 음성 `8463-294828-0037`의 동일85920 PCM16 samples를 **한 번의 clip-start부터 clip-done까지 한 xsim 실행**으로 처리했다. 실행 시작은2026-10-04 23:07:47KST, 결과 기록 완료는2026-10-05 02:16:55KST이며, Vivado와 Python 실행기 모두 exit0, 상태 `passed`다. 실제 FP7.1 Rev19/FFT9.1 Rev13/XPM vendor behavioral model을 사용했다. 구간 실행의 출력이나 cycle을 합쳐 이번 원시 trace를 만들지 않았다.

#### 고정본과 이번 변경 범위

최종 compact의6개 RTL, `ip_04`, 계수 생성기와6개 ROM, PCM, Python/C reference, log floor와 tolerance를 고정했다. 6RTL과 계수 생성기의 SHA256은 기존 compact·구간 실행과 모두 같다. 실행 전 `freeze.json`, `source/`, `coefficients/`, `inputs/`, `cases.txt`에 원본 연결과 사본을 기록했으며, 각 파일의 실제 해시는 실행별 artifact index에 있다.

| 고정 항목 | SHA256 |
|---|---|
| PCM16 원본 | `026cae2934935a27911f88d7d6e0ddcc678ad433fb50fe764233a57e4bbcde32` |
| IP04 manifest | `d3b13e3f597f53322ec13f789c117cff42d656ae707b10d69c0ede2213f545f3` |
| coefficient manifest | `fa8107d697bfcd92ee80d6603180f02524f5eb1f496ed096475b59757c3abd90` |
| tolerance | `64ad3ab3a0ae050562b9ccfab7b3c8e7fcc90eb527eb6811cfcbbd4c07a9d2b6` |
| 이번 TB | `7d797efe0b328b5916cd47166231239d3c1d67cd334a86374f18f19dd2916901` |
| 연속/구간 비교기 | `518160bd9707649a73a12607837570afa32d366e824c5c981a6b8890aad17269` |

이번 코드 변경은 `verification/fp32/tb_fp32_mfcc.sv`의 검증용 monitor와 새 `verification/fp32/compare_continuous_segmented.py`다. TB에 PCM/pre/frame/window/output stall 유지, start/end/done 수명, reset 횟수, 마지막 출력·tail·완료 후 idle 검사를 추가했다. 합성 RTL·IP·산술·입력 공백/출력 stall 발생 규칙을 바꾸지 않았다. `continuous_monitor_smoke_01`의 실제3frame 실행이 먼저 통과했고, `continuous_monitor_equivalence_01`에서 기존 compact smoke와10단계 bit 일치 및 기존 clip interval173249cycles 동일성을 확인했다. post-done64clock 검사는 그 interval 밖이다.

#### 전체 프로토콜과 완료

`simulation_summary.txt`와 `protocol_detail.txt` 모두 완료 footer가 있으며, 별도 비교기도 원시 trace의 순서·metadata·개수·유한성을 다시 검사했다.

| 검사 | 직접 관측한 결과 |
|---|---:|
| clip-start / EOF / clip-done 수락 |1 /1 /1 |
| PCM 수락 / PCM→float / pre-emphasis |각85920개 |
| frame / window 출력 sample words |각273408개 =534×512 |
| 최종 MFCC |534frames×13 =6942개; 누락·중복·순서 오류0 |
| 마지막 metadata |frame ID533,start sample85280,C12 |
| 마지막 완전 frame 종료 / 불완전 tail |sample85791까지 사용, sample85792…85919의128개 tail 폐기 |
| 초기 reset / clip 중 reset |초기1회16clocks /0회 |
| done valid hold / done 수락 후 idle 검사 |14clocks /64clocks; stale/extra 출력0 |
| input gap |21278회,63834clocks |
| PCM input backpressure |27290477clocks |
| pre / frame / window stall |24261224 /23186816 /19639453clocks |
| MFCC output stall / C12 stall |53931 /52458clocks |
| 강제 C12 stall coverage |모든534frames |

Tail128개도 PCM 변환과 연속 pre-emphasis를 정확히 한 번씩 거친다. 다음 frame 시작 후보85440부터는 overlap352개와 새 tail128개를 합친480개만 남으므로, 완전한 추가512sample frame이나 MFCC로 배출하지 않는다. 입력 gap이나 frame overlap에서 pre-emphasis를 재초기화하지 않았으며, clip 중 reset은0회다. stall 검사는 valid와 payload를 함께 확인하고, frame/window에는 index·frame ID·start·last, MFCC에는 coefficient index·frame ID·start·last를 포함한다. 완료는 EOF 수락과 마지막 C12 수락 뒤에만 허용했다.

| 동일 시뮬레이션의 이벤트 | 실제 cycle |
|---|---:|
| clip-start 수락 |34 |
| 마지막 PCM 수락 |27440265 |
| EOF 수락 |27440302 |
| 마지막 frame533/C12 수락 |27478348 |
| 최초 clip-done valid |27478350 |
| clip-done 수락 |27478364 |
| 완료 후 idle 검사 종료 |27478428 |

start 수락부터 done 수락까지 양 끝 cycle을 포함한 관측 구간은 **27478331cycles**다. 이는 이번 단일 연속 실행에서 직접 기록한 값이며 입력 gap·backpressure·출력 stall·done hold를 포함한다. 프레임 C12 수락 간격533개의 min/median/max는51419/51421/51436cycles였다. 순수 연산 지연, 실측 보드 처리 시간, 실시간 음성 입력 처리율 또는 Fmax를 측정한 값으로 쓰지 않는다.

#### 고정 기준 및 기존 분할 결과와 단계별 비교

고정된 Python float64와 PC C float32 각각에 대해 **10단계 모두 허용치 위반0,NaN/Inf0**이었다. `continuous_segmented_equivalence_01/equivalence.json`은 완료 상태·PCM·RTL/IP·계수·reference·tolerance 출처를 먼저 검증하고, 연속 원시 trace→저장 배열 및 구간 원시 trace→소유 frame 재조립을 각각 대조한 뒤 두 실행을 비교했다. 기존7개 경계의 매핑도 다시 검사했다. 비교기 exit0이며 `continuous_protocol_checks_passed`, `segmented_mapping_checks_passed`, `comparison_provenance_passed`, `all_stage_bits_identical`가 모두 true다.

| 단계 | 연속 HW−Python 최대오차 | 연속 HW−PC C 최대오차 | 연속/구간 binary32 성분 수 | bit 불일치 |
|---|---:|---:|---:|---:|
| input_float |0|0|85920|0|
| preemphasis |3.57627868e-8|0|85920|0|
| frames |3.57627868e-8|0|273408|0|
| windowed |3.97260865e-8|0|273408|0|
| complex FFT(사용257bins) |2.34836668e-6|2.31197204e-6|274476|0|
| power |7.53639202e-8|7.45058060e-8|137238|0|
| Mel energy |1.71083146e-7|1.19209290e-7|13884|0|
| log-Mel |5.44364056e-4|5.07354736e-4|13884|0|
| DCT |2.70097533e-4|2.70843506e-4|6942|0|
| MFCC C0…C12 |2.70097533e-4|2.70843506e-4|6942|0|
| 합계 |—|—|1172022|0|

FFT는 실수부·허수부를 별도32-bit 성분으로 센다. MFCC의 HW−Python RMSE는 `2.7910545339234584e-5`, HW−PC C RMSE는 `2.7653654441061345e-5`다. 최대 Python 오차는 frame489/C1의 `0.00027009753311091345`이며 허용폭은 `0.001249557029893788`이다. 전체 단계의 RMSE·요소별 위반 수·worst index는 새 실행의 `comparison.json`에 있다. 모든 배열이 기존 구간 결과와 bit 동일하므로 위 구간 실행 표의 단계별 RMSE와도 같다. **HW−구간 HW의 bit 동일성과 HW−PC C의 허용치 통과는 구분**한다. PC C와 최종 MFCC의 bit 일치는 여전히240/6942개다.

새 연속 실행의 대표 그림은 `figures/8463-294828-0037_heatmaps.png`, `figures/8463-294828-0037_coefficient_errors.png`다. 실제 생성 그림을 열어534frames/C0…C12, Python/C/HW 및 차이 패널과 계수별 오차 라벨을 확인했다. 차이 패널별 색상 범위는 서로 다르다.

연속 실행 artifact index SHA256은 `ecc510646d9c75c496f4850b09a9943fa9df78f099d67b7ff5e639b8c647d7ac`다. 별도 연속/구간 비교 artifact index는 `c85583632edc431dcd5409479d9a597ea6ec5ef206def0762b3109a2e3dc6f30`이다. 원시 trace·protocol detail·고정본·단계별 배열·그림은 모두 새 실행 폴더에 보존했다. 기존 분할 실행과 합성 실패 실행은 덮어쓰지 않았다.

`continuous_final_audit_01`의 독립 감사에서 새 연속 실행402개(159855186bytes), 기존 구간 실행619개(365900129bytes), 최종 합성 입력 실행620개(118052398bytes), **총1641개 indexed file을 전수 재hash해 불일치0**을 확인했다. 원본 연결까지 포함한1945개 해시 검사와113개 metadata/trace 검사도 통과했다. 연속 raw trace1034784행의10단계 개수·순서·유한성·cycle 순서·저장 배열 바이트 일치를 별도로 확인했다. 현재6RTL/IP04/계수/허용치/Python·C 기준과 기존 합성5개 실패의 보존도 검사했다. 감사 스크립트 첫 시도의 지역 변수명 충돌은 해시 검사 시작 전 발생했으며 `attempt_01.json`에 보존했고, 검사기 수정 후 완료한 결과만 통과로 기록했다.

이번 결과는 개발 음성1개의 연속 경로 검증을 완료한 것이다. 기존 합성5개 실패(`composite_500_2237hz`, `dc_negative_8192`, `dc_positive_8192`, `fullscale_alternating`, `tone_bin32_1000hz`)는 그대로 남는다. 수치 형식·log floor·허용치를 바꾸거나 새로운 최적화를 하지 않았고, 보드 실행과 평가 음성20개 확장도 수행하지 않았다.

### F3: dense-CE 회로 합성·OOC 배치배선

`build/fp32_hw/synth_ce_01`은 Mel 압축 전6RTL과 IP04로 synthesis/opt/place/phys_opt/route를 완료했다(exit0). 실행 후 source_changed_after_snapshot=[]였으며 당시 RTL 해시와 일치했다. 네 계수 ROM 초기 파일 로드도 합성 로그에서 확인했다. Compact 변경 후 현재 backend와는 해시가 다르며, 새 결과는 `synth_compact_01`에서 별도 확인한다.

| routed 자원/검사 | 직접 확인한 결과 |
|---|---:|
| LUT |4353/53200 (8.18%)|
| FF |7796/106400 (7.33%)|
| BRAM36 환산 tile |15/140 (10.71%):8RAMB36+14RAMB18|
| DSP |26/220 (11.82%)|
| latch / distributed RAM |0 / 0|
| 10ns clock WNS / TNS |+2.448ns /0|
| hold WHS / THS |+0.021ns /0|
| routable nets |10820/10820,routing errors0|

이는 **BUFGCTRL_X0Y0를 clock origin으로 가정한 component OOC 내부 경로의100MHz timing 통과**다. 실제 BUFG/IOB는 이 OOC top에0개이며 board bitstream이 아니다. unconstrained internal endpoints0, 외부22input/109output에는 아직 timing delay/partition pin이 없다. 따라서 PS/PL·보드 IO를 포함한 timing closure나 Fmax로 확대하지 않는다. `[Synth 8-7080]` parallel synthesis criteria 경고는 도구 실행 조건이며 기능 실패가 아니다.

DRC에 critical/error는 없었다. PDCN1569×46은 vendor log-IP LUT equation의 미사용 입력 pin, ZPS7-1×1은 이 PL component에 PS7이 없는 데 따른 경고, AVAL4×3은 DSP operand register 권고다. 의도적으로 무시한 신규 latch/폭/다중-driver/일반 clock-gating 오류는 없다. Vendor/XPM의 timescale·unused-port·simulation collision-reporting 안내는 보존했다. CE 이전 `synth_03`은 clock origin 미지정, `synth_04`는 같은 이전 회로에 origin을 지정한 이력이므로 최종 CE 결과와 구분한다.

세부 보고서: `synth_ce_01/reports/{utilization_routed,timing_routed,drc_routed,route_status}.txt`, `fp32_mfcc_routed.dcp`, `project/fp32_mfcc.xpr`. 재현 명령과 실패 이력은 [SYNTHESIS_FLOW.md](../hardware/fp32/ip/SYNTHESIS_FLOW.md)에 있다.

### F3: 최종 compact 회로 합성·OOC 배치배선

`synth_compact_01`은 현재6RTL/IP04로 synthesis/opt/place/phys_opt/route를 완료했다(exit0). 현재 RTL과 freeze의 해시가 일치하고 source_changed_after_snapshot=[]다. 실제 사용한5ROM(window/Mel compact/descriptor/DCT/scale)의 초기 파일 로드를 로그에서 확인했다. 사용하지 않는 dense Mel 감사 파일도 별도로 해시를 남겼다.

| routed 자원/검사 | 최종 compact 결과 | dense-CE 대비 |
|---|---:|---:|
| LUT |4379/53200 (8.23%)|+26|
| FF |7801/106400 (7.33%)|+5|
| BRAM36 환산 tile |8/140 (5.71%):16RAMB18,0RAMB36|−7|
| DSP |26/220 (11.82%)|0|
| latch / distributed RAM |0 /0|0|
| 10ns WNS /TNS |+0.942ns /0|각 회로의 개별 결과|
| WHS /THS |+0.023ns /0|각 회로의 개별 결과|
| routable nets |10835/10835,errors0|전체 배선|

위와 동일하게 `BUFGCTRL_X0Y0`를 가정한 내부 OOC timing이다. 외부 입력22개/출력109개의 delay는 미지정이므로 board IO timing closure·Fmax·실제 처리 시간으로 확대하지 않는다. DRC의 vendor LUT/PS7 없음/DSP register 권고는 별도 보고서에 보존한다.

`resource_delta_01`에서 dense/compact의 synth/routed DCP 네 개를 직접 읽어 DSP 계층을 대조했다. 두 회로 모두 synth28→routed26이며, window가 MUL opcode만 사용하여 제거되는 `U_WINDOW/U_ALU/U_ADDSUB`의 DSP2개가 차이의 전부다. Mel 압축으로 DSP나 산술 IP 설정을 바꾼 결과가 아니다. 서로 다른 합성/배선 단계의 자원 수치를 같은 기준으로 비교하지 않는다.

## 4. 아직 미검증인 사항

실제 보드의 PL 실행·클록 계측·전력·성능·PS/DMA 연결·마이크/I2S 입력은 이번 범위가 아니다. 새 XSA/bitstream, QSPI/SD 쓰기는 수행하지 않는다. 보드 입출력 timing·PS/PL 통합 timing도 OOC 결과와 구분한다. ARM 보드 실행의 미검증 상태는 [ARM_BRINGUP_RESULTS.md](ARM_BRINGUP_RESULTS.md)를 유지한다.

## 5. 재현·보존 근거

실행마다 새로운 run ID를 사용한다. 다음 명령은 이미 존재하는 최종 실행 폴더를 덮어쓰지 않도록 예시 이름을 썼다. Python 환경은3.11.9, NumPy2.2.6, SciPy1.15.3, Matplotlib3.10.3이며 Vivado는2024.2 SW5239630/IP5239520다.

```powershell
$fp32Python = 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe'
$fp32Root = 'D:\2610_MFCC\project'
$fp32Ip = 'D:\2610_MFCC\build\fp32_hw\ip_04'
& $fp32Python "$fp32Root\scripts\run_fp32_reference.py" --run-id new_synthetic --selection synthetic --mode sim --ip-run $fp32Ip --reset-stress
& $fp32Python "$fp32Root\scripts\run_fp32_reference.py" --run-id new_smoke --selection smoke --mode sim --ip-run $fp32Ip
& $fp32Python "$fp32Root\scripts\run_fp32_segmented.py" --run-id new_development --snapshot-run D:\2610_MFCC\build\fp32_hw\new_smoke --workers 8 --execute
& $fp32Python "$fp32Root\scripts\run_fp32_reference.py" --run-id new_continuous_development --selection development --mode sim --ip-run $fp32Ip
& $fp32Python -B "$fp32Root\verification\fp32\compare_continuous_segmented.py" --continuous-run D:\2610_MFCC\build\fp32_hw\new_continuous_development --segmented-run D:\2610_MFCC\build\fp32_hw\segmented_development_01 --output D:\2610_MFCC\build\fp32_hw\new_continuous_equivalence
& $fp32Python "$fp32Root\scripts\run_fp32_ip_synth.py" --run-id new_synthesis --ip-run $fp32Ip
```

`scripts/build_fp32_ips.ps1 -RunId new_ip`로 IP를 다시 생성할 수 있다. 위 예시는 완료되어 검증한 `ip_04`를 재사용한다. 각 명령의 성공 여부를 확인한 뒤 다음 단계로 진행한다. Python 실행기의 return code2는 수치 비교를 완료했으나 실패 항목이 있다는 뜻이다. 연속/구간 비교기의 return code2는 bit 차이,1은 출처·검사 계약 오류다. 비교 시 `--expected-continuous-index-sha256 <완료 index의 SHA256>`을 추가해 대상 실행도 고정할 수 있다. 이번 실제 실행 ID는 `continuous_development_01`, 비교 ID는 `continuous_segmented_equivalence_01`이며, 완료 index 해시를 명시해 비교했다. 단일 클립 MFCC 검사는 별도로 `--selection development`를 사용하며, 구간 결과로 그 실행을 대신하지 않는다.

모든 경로는 `D:\2610_MFCC\build\fp32_hw\` 아래다. 실행의 `freeze.json`/`run_manifest.json`, `artifact_manifest.json`, source snapshot, PCM slice/해시, ROM/계수 manifest, 실제 IP trace와 comparison 파일을 함께 보존한다. 현재 backend SHA256은 `c556a8e85f30b4914ef23c523017a719be2ffc752499be7e25a4ae549201ab6b`이며 `synth_compact_01`, 최종 compact smoke와 전체 합성/음성 실행이 같은 회로를 사용한다. 생성 프로젝트는 `synth_compact_01/project/fp32_mfcc.xpr`에 있다.

`preservation_audit_01/audit_results.json`에서 Python/C artifact index2개, C core4개, 기존 ARM XSA/ELF3개, GitHub 원본6개를 다시 읽어 **15/15 SHA256 일치**를 확인했다. 이 감사는 선택한 핵심 파일의 보존 근거이며 모든 과거 실행 파일을 전수 재검증했다는 뜻은 아니다. 기존 snapshot·실패 실행은 덮어쓰지 않았고 Git commit/push는 수행하지 않았다.

`final_synthetic_audit_01`은 최종 합성 입력 실행/비트 비교/수치 진단의 artifact index에 있는620/620,2/2,7/7개 파일을 전수 재검사했다. 원본 연결까지 포함한1293개 해시 검사와198개 metadata 검사에서 불일치0이었다. 현재6RTL, IP04의227개 산출물, imported5XCI의 component/model parameters,6ROM과 고정 C 계수의 연결도 확인했다. Imported XCI는 생성 프로젝트 경로 때문에 raw hash가 다를 수 있으므로 parameter/VLNV/revision을 별도로 대조했다. 이 감사는 수치 실패를 통과로 바꾸지 않는다.
