# MFCC 소스·도구 감사

> 이 문서가 인용하는 로그는 저장소에 포함되지 않는 로컬 감사 폴더에 있다. 본문에서는 `<감사 폴더>`로 표기한다.

작성: 2026-10-04, Asia/Seoul. 대상: ZYBO Z7-20의 AI 음성 특징 추출 및 재현 가능한 네 구현 비교. 공통 수치 규격은 [MFCC_SPEC.md](MFCC_SPEC.md)를 따른다.

**결론:** 참고 GitHub의 MFCC 계수와 주요 IP 설정은 확보되어 있다. 그러나 배포된 top은 39개 특징을 DNN에 넘기는 Kintex VAD 설계이며, 그대로 ZYBO MFCC 완성품으로 취급할 수 없다. Vivado **2024.2에서 authored HDL 15개의 구문 분석은 통과**했지만, Zynq 대상의 **IP 9개는 모두 locked/Upgrade IP 권고**였다. 전송·reset·CDC·pre-emphasis 정렬·IP 재생성을 검증해야 한다. 우선 저장 PCM에서 raw 13계수까지 Python/C를 완성하고 ARM에서 회수하는 전체 경로를 확보한 뒤, 동일 경계를 PL에 이식한다. 네 비교군의 구현·검증·보드 동작은 아직 완료 사실이 아니다.

사용자 추가 확정 조건은 **특정 음성 파일 공통 입력, 마이크/I2S/코덱 제외, 목표 Vivado 2024.2**이다. 초기 감사 때 음성 파일은 미지정이었다. 후속 자료 준비에서 LibriSpeech test-clean 21개를 확보했으며 현재 입력은 D:/2610_MFCC/data/librispeech/DATASET_MANIFEST.json을 따른다. 입력 확보가 아래 HDL 감사나 MFCC 실행 상태를 변경하지는 않는다. 저장 PCM을 PS 메모리에 적재하고 ARM 또는 실제 PL에서 MFCC를 실행·회수한다는 목표는 유지한다. Vitis/ARM 플랫폼 호환성은 별도 미확인 항목이다.

## 1. 상태 정의와 이번 작업 범위

| 표기 | 의미 |
|---|---|
| 소스에서 확인 | 파일에 존재하는 연결·상수·설정 또는 문서 기록. 성공 기록을 읽은 것과 재실행한 것을 구분 |
| 설계 제안 | 공통 규격·이식·일정에 대한 이번 판단. 구현/측정 결과가 아님 |
| 미확인 | 기능 실행·호환성·보드·성능 또는 결정 근거 부족 |
| 이번 직접 검사 | 이 감사에서 실행한 hash 비교·정적 수치 검사·도구 명령의 결과. 범위를 함께 표시 |

먼저 `README.md`, `docs/DEVELOPMENT_HANDOFF.md`, `docs/FIRST_TASK.md`, `docs/REFERENCE_SOURCES.md`를 읽었다. 작업 시작 Git 상태는 깨끗했고 HEAD는 `e679534de5b2f9f2222dc06d3f9ac0a4a7fe8db9` (`docs: record reference snapshots and handoff status`)였다. 개발 저장소에는 구현 소스가 아직 없고 문서와 폴더 골격이 있다. 작업 위치와 상위에서 적용되는 AGENTS.md는 검색되지 않았다.

이번 변경 파일은 이 문서와 `MFCC_SPEC.md` 두 개다. 실행 점검용 소수의 소스·설정 복사본과 로그는 `<감사 폴더>`에만 뒀다. 원본 reference, FFT 검토 파일, RTL 구현, Git commit/push, 프로젝트 전체 복제, 논문·발표 자료는 변경하지 않았다.

## 2. 소스 식별과 무결성

| 항목 | 확인 내용 | 증거 수준 |
|---|---|---|
| GitHub 보관본 | `D:\2610_MFCC\reference_code\github_mfcc` | 소스에서 확인; Git clone이 아닌 ZIP 추출본 |
| 출처 | [AlexKly의 Kintex MFCC VAD](https://github.com/AlexKly/Simple-Voice-Activity-Detector-using-MFCC-based-on-FPGA-Kintex) | manifest에 기록된 원본 출처; 원격 최신 HEAD로 대체하지 않음 |
| snapshot | `27aa09974049d11f49383c8e18f3ca9e34d08ed9` | manifest의 ZIP 주석 기록; 이번에 원격 이력/ZIP 주석을 다시 검증한 것은 아님 |
| ZIP SHA-256 | `3a00d8668a325a6efb62cf0b8ca505d9a2298ecc494523f12d7fe0bb5296039c` | 기존 manifest 기록; ZIP CRC 성공도 기존 준비 기록 |
| 보관본 무결성 | GitHub 실제 520파일, manifest 520파일, 크기/SHA-256 불일치 0 | **이번 직접 검사**: 보관 파일과 manifest 비교. 알고리즘 검증 아님 |
| 기존 FFT | `D:\2610_MFCC\reference_code\previous_fft`; 원래 위치 `D:\20260824_FFT` | manifest 전체 12,167파일. 이번 전체 hash 재검사는 안 함 |
| FFT 부분 무결성 | `rtl/` 20파일 + README/open_issues/phase8_integration_status 3파일의 SHA-256 일치 | **이번 직접 검사**: 선택 23파일만 manifest와 비교 |
| manifest | [SOURCE_MANIFEST.json](D:/2610_MFCC/reference_code/SOURCE_MANIFEST.json) | 이번 파일 hash `50f31702cc7ddc872399d702a6bda61c4f4cdad78e20e8797e5214b1748ef307` |
| 라이선스 | GitHub 보관본에서 LICENSE/LICENCE/COPYING 독립 파일 미발견 | 소스에서 확인; 수정·재배포 조건 미확인. 개발 저장소 반입 전 확인 필요 |

hash 검사는 PowerShell `ConvertFrom-Json`, 각 manifest entry의 `Get-Item.Length`, `Get-FileHash -Algorithm SHA256`를 비교했다. FFT 선택 조건은 `^(rtl/|README.md$|docs/open_issues.md$|reports/phase8_integration_status.md$)`이다. 원본 다운로드의 진위나 저작권 허락을 hash 일치로 증명하지 않는다.

아래 줄 번호는 보관본 기준이다. `G`는 GitHub 보관 디렉터리, `M`은 `G/FPGA source/Calculation MFCC features`, `I`는 `G/FPGA source/IP cores`, `F`는 기존 FFT 보관 디렉터리를 뜻한다.

## 3. 입력부터 출력까지 실제 연결

**소스에서 확인.** [`Vega_submain.v`](<D:/2610_MFCC/reference_code/github_mfcc/FPGA source/Vega_submain.v:58>)의 입력은 `g_fast_clk,bclk,wclk,d_audio`, 외부 출력은 `DNN_Done,DNN_Result`이다. 외부 MFCC 출력 포트나 PS 연결은 없다.

```text
capture_audio_sample (I2S 16-bit capture)
  → floating_point_int16_to_float32
  → MFCC
      Pre_emphasis [bclk]
      → get_frames [bclk → g_clk, 512/160]
      → windowing [g_clk]
      → power_spectrum [FFT → square/sum → sqrt → square → /512, 257 bins]
          ├→ energy → ln energy ─────────────────────┐
          └→ filterbanks → zero replacement → ln    │
               → dct_type2 → coef_dct2              │
                   [raw C0…C12 비교 지점]           │
               → lifter → appendEnergy [C0 교체] ←──┘
               → delta_feat [delta 두 단계, 총39개]
  → DNN_0 → 판정 LED용 결과
```

`ila_Vega_subModule`은 I2S 입력 관찰용 분기다. 이번 파일 입력 구현에는 I2S capture, DNN, ILA가 필요하지 않다. 입력 변환 IP는 향후 signed PCM을 PL에서 float32로 바꿀 때만 필요하며 /32768 정규화는 별도이다.

| 경계 | 파일·줄 및 실제 신호 | 판정 |
|---|---|---|
| 입력 capture/변환 | `G/FPGA source/Vega_submain.v:58–82`; `capture_audio_sample.v:37–55` | 소스에서 확인: wclk=0에서 16bit capture. ADC 구동 HDL 미첨부는 README:17의 설명 |
| 본체 전처리 | `M/MFCC.v:85–122` | pre-emphasis→frame→Hamming→power 연결 |
| Mel/log/DCT | `M/MFCC.v:134–188` | raw13 출력 후보는 `dct2_feat/tvalid_dct2_feat` |
| energy 분기 | `M/MFCC.v:125–132` | power에서 분기; raw13 계산에는 불필요 |
| lifter/C0 교체 | `M/MFCC.v:190–209`; `appendEnergy.v:105–108` | append는 14번째 값을 더하지 않고 첫 계수를 ln energy로 교체 |
| 추가 특징 | `M/MFCC.v:213–223`; `delta_feat.v:53–55,414–418,468–488` | 13 정적 +13 delta +13 delta-delta를 직렬 출력 |
| DNN | `G/FPGA source/Vega_submain.v:96–107`; `G/C++ source/DNN_main.cpp:3` | C++ 소스는 MFCC C 모델이 아니라 DNN 추론. C MFCC는 별도 구현 필요 |

**설계 제안:** 주 비교는 `coef_dct2` 직후 13개로 고정한다. 원본 VAD 재현 시 lifter22/energy replacement/39개 출력은 별도 profile로 구분한다. 이 변경으로 기존 DNN 모델 입력과 호환된다고 주장하지 않는다.

## 4. 수치 설정과 필요한 계수

[`DNN modeling.ipynb`](<D:/2610_MFCC/reference_code/github_mfcc/Python source/DNN modeling.ipynb:81>)의 `cells[]`는 0기반으로 셌다. cells[3]에 Fs16000, frame .032, hop .01, alpha .95, np.hamming, NFFT512, nfilt26, numcep13, lifter22, appendEnergy=True가 있다(원문 81–90행). cells[10]은 이를 `mfcc()`에 전달하고 delta N=2를 두 번 호출한다(206–229행). cells[13]은 WAV/resampling을 사용하며 /32768은 명시되지 않는다(295–296행). 데이터 절대 경로는 포함되지만 실제 학습 WAV/CSV가 공통 입력으로 확보됐다는 뜻은 아니다. 패키지 버전 고정도 없다.

| 항목 | 소스에서 확인 | 이번 직접 수치 검사 / 미확인 |
|---|---|---|
| rate·clock 의도 | TB:66–68에 wclk 62.5μs=16kHz, bclk 1.953125μs=512kHz, fast 9.259ns≈108MHz | TB 상수이며 보드 측정 아님. XCI 목표 clock과도 구분 |
| frame/hop | `M/get_frames.vhd:51–52`: 512/160 | 실제 첫 프레임·겹침·CDC 출력 미검증 |
| pre-emphasis | `M/Pre_emphasis.v:33`: `bf733333`≈−.95 | 상수 decode 확인; 이전 유효 샘플 정렬 미검증 |
| Hamming | `M/windowing.v:43–554`, 512개 | `.54-.46*cos(2πn/511)`를 binary32로 변환한 값과 불일치0 |
| FFT | `I/fft_block/fft_block.xci:53–70`, 512/float32/natural_order/realtime/pipelined_streaming | `scaling_options=scaled`, phase24, rounding 문자열 truncation. 실제 float FFT 이득은 impulse 시험 필요 |
| power | `M/power_spectrum.v:100–167,186–203` | sqrt(제곱합) 후 재제곱 /512, 첫257개. 직접 제곱합과 rounding 차이 미검증 |
| Mel | `M/filterbanks.v:207–6888`, 26×257=6682개 | HTK Mel, 0–8kHz, floor(513*f/16000), 삼각형 피크1, 면적 정규화 없음; binary32 불일치0 |
| log | `M/MFCC.v:145–160`; IP operation Logarithm | +0 비트패턴만 `25800000=2^-52`로 교체. 일반 max floor가 아니며 float32 epsilon도 아님 |
| DCT-II | `M/dct_type2.v:131–468`, 13×26=338개 | cos(πc(m+.5)/26)의 binary32와 불일치0 |
| DCT normalization | `M/coef_dct2.v:46–58`, 13개 | c0=√(1/26), 나머지 √(2/26), binary32 불일치0 |
| lifter | `M/lifter.v:46–58`, 13개 | L22 식 `1+11*sin(πc/22)`, binary32 불일치0; 주 비교 제외 |

**계수 파일은 누락된 것이 아니다.** hand-written MFCC 코드에서 `$readmem*`/외부 `.coe/.mem` 참조를 발견하지 않았으며 위 표의 테이블이 HDL에 내장되어 있다. 별도 계수 파일 복구가 선행 blocker가 아니다. 새 구현에서 테이블을 파일로 생성하는 것은 재현성 개선 제안이다.

수치 검사는 PowerShell/.NET에서 `assign ...[index] = 32'hXXXXXXXX`를 regex로 추출하고, `Convert.ToUInt32(hex,16)`→`BitConverter.GetBytes`→`BitConverter.ToSingle`로 decode한 뒤 `Math` double 수식의 `[single]` 변환과 비교했다. Hamming/Mel/DCT/scale/lifter의 비교 개수는 각각 512/6682/338/13/13이며 불일치는 각각0이다. RTL의 시간 동작·누산 결과를 실행한 것이 아니다. 전체 Mel bin 및 재현 수식은 MFCC_SPEC에 있다.

## 5. raw13 경로의 IP 목록과 보존물

**소스에서 확인:** 14개 IP 디렉터리에 `.xci`와 생성 소스/산출물이 있다. FFT는 xfft9.0, FP는 floating_point7.1이다. XCI는 Vivado2016.2, Kintex7 `xc7k325t/ffg900/-2`, simulator MIXED를 기록한다. README의 불완전한 part 문자열보다 실제 XCI를 근거로 삼았다(`fft_block.xci:72–89`, `floating_point_mult.xci:126–143`).

| IP 이름 | 사용 위치 | 원본 설정 / 필요성 |
|---|---|---|
| `fft_block` | power_spectrum | 512/float32/natural_order/realtime. reset·clock enable false. config `16'h0001`(`power_spectrum.v:61`) |
| `floating_point_mult` | pre-emphasis, window, power | Blocking, latency1, binary32; raw13 필수 |
| `floating_point_add` | pre-emphasis, power | Blocking, latency1; raw13 필수 |
| `floating_point_square_root` | power | Blocking, latency1; 원본 구조 유지 시 필요. 직접 제곱합으로 바꾸면 제거 후보 |
| `floating_point_div` | power /512 | Blocking, latency1; raw13 필수(원본 구조) |
| `floating_point_mult_non_blocking` | Mel·DCT 및 DCT scaling | NonBlocking, latency0; raw13 필수 |
| `floating_point_add_non_blocking` | Mel·DCT 누산 | NonBlocking, latency0; raw13 필수 |
| `floating_point_log_non_blocking` | Mel log | NonBlocking, latency0; raw13 필수 |
| `floating_point_int16_to_float32` | top PCM 변환 | NonBlocking, latency0; PCM wrapper에서 필요 여부 결정 |
| `floating_point_log` | energy | Blocking, latency1; raw13에서 제외 |
| `floating_point_sub` | delta | Blocking, latency1; raw13에서 제외 |
| `DNN_0`, HLS ZIP | 분류 | raw13에서 제외; `so-logic_hls_DNN_0_0.zip` 보관됨 |
| `ila_Vega_subModule` | 디버그 | 필수 계산 경로 아님 |
| `floating_point_comp` | 배포 IP | hand-written MFCC/top에서 사용 인스턴스 미발견 |

FP 설정의 근거는 각 XCI의 `C_Latency`, `Flow_Control`, `Has_ARESETn`, `Has_RESULT_TREADY`(주로 101,109,111,120행)다. reset/result-ready는 false이다. **Latency0의 nonblocking IP가 aclk 없이 연결된 것은 현재 설정과 일치**한다. clock 누락 결함으로 기록하지 않는다. 다만 IP 재생성에서 latency가 달라지면 누산 feedback과 제어를 함께 수정해야 한다.

소스 전체에서 standalone `.xpr`, 프로젝트 재현 `.tcl`, top-level 보드 XDC는 발견하지 못했다. 있는 `.xdc`는 IP OOC/내부 제약이다. 현재 사용자 PC의 별도 수정 Vivado 프로젝트 위치도 이번 입력에 없으므로 보관본과 같다는 가정으로 검증 완료 처리하지 않는다.

## 6. 이식에서 먼저 해결할 동작 위험

아래는 소스에 근거한 **위험 또는 미확인 동작**이다. 시뮬레이션으로 숫자 오류를 재현한 결과가 아니다.

| 근거 | 관찰 및 영향 | 설계 제안 / 필요한 검증 |
|---|---|---|
| `Pre_emphasis.v:46–74` | 데이터와 valid를 매 bclk 1클록 지연하여 원래 입력과 Blocking add에 공급. 이전 accepted sample 저장과 다름 | 임펄스 기대 `[1,-.95,0,…]`, valid gap을 포함해 operand pairing 검증. 직전 유효 샘플 상태를 명시 |
| `get_frames.vhd:68–124` | slow-domain flags와 512×32 frame bus를 fast domain에서 직접 읽음; handshake/synchronizer 미발견 | 파일 입력은 단일 PL clock+sample enable 또는 검증한 buffer로 구성 |
| `power_spectrum.v:69,90,175–178` | FFT input TLAST wire에 driver 없음; ready에 무관하게 입력 전진; 이벤트 미소비 | 프레임 카운터·TLAST·accepted beat 정합·event 검증 |
| `power_spectrum.v:186–203` | 첫257개 통과 카운터가 valid 공백에서 초기화 | 연속 프레임·중간 stall에서 bin/경계 오류 가능. 명시적 frame/bin ID 사용 |
| `filterbanks.v:7601`, `dct_type2.v:858` | 각각 negedge valid, negedge 제어신호를 clock으로 사용 | g_clk 동기 상태기 또는 파생 clock 제약 필요; 단순 part 변경으로 해결되지 않음 |
| `MFCC.v:97,107,117,127,137,173,183,193,203,215` | reset/rst가 모두0; 일부 leaf reset도 실제 상태 전체를 초기화하지 않음 | 프레임 중단·재시작·첫 프레임과 accumulator 초기화 검증 |
| `energy.v:34,109–133` | 512 기준을 valid가 아니라 clock으로 세고 power valid는257개 | raw13에서는 제외. 후속 energy 누산·완료시점 시험 필요 |
| `appendEnergy.v:105–108,136–143` | 한 cycle energy flag를 WAIT 상태에서만 사용, 미리 온 값 보존 없음 | 후속 profile에서 frame tag와 energy holding register 필요 |
| `Vega_submain.v:96–107` | DNN의 address/CE를 사용하지 않고 serial MFCC를 memory q0에 연결 | DNN memory interface 검증 필요. 이번 MFCC 경로에서는 제외 |

26개 병렬 Mel mul/add와 13개 병렬 DCT mul/add, latency0 FP 연산은 Zynq 자원·timing 위험의 근거다. 합성 전 LUT/DSP 수나 최대 주파수, ‘ZYBO에 들어가지 않는다’는 결론은 내리지 않는다.

## 7. Kintex → ZYBO Z7-20 및 Vivado 2024.2

| 항목 | 소스/공식 자료에서 확인 | 필요한 변경·판정 |
|---|---|---|
| 소자 | 원본 xc7k325tffg900-2 → ZYBO `xc7z020clg400-1` | 새 target으로 IP output products 재생성. Kintex DCP 재사용 불가 |
| 도구 | 원본2016.2, 사용자 목표 **2024.2** | 복사본에서 read_ip/report_ip_status→필요 upgrade/regenerate→port/latency/numeric 설정 비교→회귀 검증 |
| 보드 제약 | 원본 board-level XDC 없음 | PS preset·필요 clock/reset/IO 제약을 ZYBO 공식 자료에서 확보. 원본 I2S pin 배치 이식 제외 |
| 클록 | 원본 TB fast≈108MHz, FFT XCI target54MHz. ZYBO PS ref33.3333MHz, 외부 PL ref125MHz | 첫 저장 PCM PS/PL 경로는 PS FCLK0 100MHz 제안. 실제 timing 통과값 아님 |
| 리셋 | 원본 top reset 포트 없음 | PS reset 및 clock-domain별 동기 해제; 상태·valid·카운터 일관 초기화 |
| PS/PL 인터페이스 | 원본은 I2S→DNN이며 PS 경로 없음 | PS 메모리 PCM→PL 입력 buffer→raw13 결과 buffer→PS 회수 추가; DMA는 후속 |
| 언어 | 핵심 get_frames는 VHDL, 다른 authored MFCC는 주로 Verilog; XCI MIXED | **VHDL이라는 이유로 재작성하지 않음.** CDC/reset/호환성을 수정 근거로 삼음 |
| VHDL package | `get_frames.vhd:24–25`에 STD_LOGIC_UNSIGNED와 NUMERIC_STD | 타 시뮬레이터 이식 시 package 호환 확인. 전체 언어 변환의 근거 아님 |

Digilent 매뉴얼에 PL 125MHz는 Ethernet PHY reset의 영향을 받는다고 명시되어 있다. 파일 입력 PS 기반에서는 FCLK 사용을 우선 검토한다. 실제 보드 연결·clock 측정·DDR/MIO preset 적용은 미확인이다. 보드 preset이 없더라도 part 지원과는 별개이며, DDR/MIO 값을 추측해서 플랫폼을 만들지 않는다.

공식 근거(2026-10-04 열람): [Digilent Zybo Z7 매뉴얼](https://digilent.com/reference/_media/reference/programmable-logic/zybo-z7/zybo-z7_rm.pdf), [공식 Z7-20 board.xml](https://github.com/Digilent/vivado-boards/blob/master/new/board_files/zybo-z7-20/A.0/board.xml), [AMD 2024.2 IP 업그레이드 절차](https://docs.amd.com/r/2024.2-English/ug896-vivado-ip/Upgrading-IP), [IP simulation model 제공](https://docs.amd.com/r/2024.2-English/ug896-vivado-ip/Delivering-IP-Simulation-Models), [mixed-language 경계 제한](https://docs.amd.com/r/2024.2-English/ug900-vivado-logic-simulation/Restrictions-on-Mixed-Language-in-Simulation). board 파일의 향후 사용 버전/commit은 별도로 고정해야 한다.

## 8. 로컬 도구와 직접 실행 검사

상세 실행 명령·stdout·exit는 [tool_versions.txt](<감사 폴더>/toolcheck/tool_versions.txt), [additional_tools.txt](<감사 폴더>/toolcheck/additional_tools.txt)에 있다. PATH 검색 실패는 PC 전체에 미설치라는 뜻이 아니다.

| 도구 | 이번 직접 확인 | 범위와 한계 |
|---|---|---|
| Vivado2024.2 | `C:\Xilinx\Vivado\2024.2`, build5239630 | 목표 버전. `-version` 출력은 있으나 기록한 wrapper exit1; 아래 별도 배치 검사와 구분 |
| XSIM2024.2 | 같은 bin, v2024.2.0, version exit0 | simulator version 확인; MFCC 기능 sim 통과 아님 |
| Vivado2020.2 | `D:\Xilinx\Vivado\2020.2`, build3064766, version exit0 | 초기 참고 점검. 배치·HDL 분석 exit0 |
| XSIM2020.2 | simulator2020.2 banner; 기록한 version wrapper exit1 | 전체 sim 미실행 |
| Vitis2024.2 | `C:\Xilinx\Vitis\2024.2`; launcher -h exit0, XSCT banner2024.2.0/build5239620 | -v는 미지원. 플랫폼/BSP/XSA/ELF 연결·빌드 미검증 |
| Vitis2020.2 | D: 설치본 version2020.2/build3064766; wrapper exit−1 | 과거 참고용. version 출력만으로 정상 개발 환경 판정하지 않음 |
| ARM GCC | 2024.2 bundled13.3.0 / 2020.2 bundled9.2.0, --version exit0 | 컴파일러 실행 확인, ARM MFCC 빌드·보드 실행 미실행 |
| PC C | MSVC19.42.34435 x64 banner | cl 기본 PATH 미발견, 절대경로 실행. 이번 컴파일 미실행 |
| 대체 simulator | ModelSim Altera10.5b version exit0 | Xilinx library/라이선스·전체 설계 호환 미검증. iverilog/verilator/ghdl PATH 미발견 |
| Python | 사용자 설치3.11.9 실제 실행 | numpy/scipy/librosa/matplotlib 미발견. Store py 항목은 등록돼 있으나 프로세스 생성 실패 |
| bundled Python | 3.12.14, numpy2.3.5 | scipy 없음; 프로젝트 재현 환경으로 고정한 것이 아님 |

사용자 Python 실제 경로는 `C:\Users\rlagk\AppData\Local\Programs\Python\Python311\python.exe`이다. 설치나 패키지 변경은 수행하지 않았다. 수치 상수 검사는 Python 실행에 의존하지 않고 PowerShell/.NET으로 했다.

### 2020.2 참고 구문·part 검사

15개 authored HDL 파일(Verilog14 + `get_frames.vhd`)만 `<감사 폴더>/toolcheck/syntax`에 복사했다. 작업 폴더에서 다음을 실행했다.

```powershell
& 'D:\Xilinx\Vivado\2020.2\bin\xvhdl.bat' get_frames.vhd
$auditVerilog = @(Get-ChildItem -LiteralPath . -Filter '*.v' -File |
    Select-Object -ExpandProperty Name)
& 'D:\Xilinx\Vivado\2020.2\bin\xvlog.bat' @auditVerilog
```

둘 다 exit0. [명령 기록](<감사 폴더>/toolcheck/syntax/COMMANDS.txt), [xvhdl.log](<감사 폴더>/toolcheck/syntax/xvhdl.log), [xvlog.log](<감사 폴더>/toolcheck/syntax/xvlog.log). **분석 성공만 확인했으며 elaboration, mixed-language binding, IP 연결, 수치 sim, 합성, 구현, 보드 동작을 실행하지 않았다.**

별도 `query_device.tcl`을 Vivado2020.2 batch로 실행한 결과(exit0), `get_parts -quiet xc7z020clg400-1`은 해당 part를 반환했고 `get_board_parts -quiet *zybo*z7*20*`는 비어 있었다. [device_query_2020_2.log](<감사 폴더>/toolcheck/device_query_2020_2.log). 이 결과를 2024.2 catalog 확인으로 사용하지 않는다.

### 2024.2 목표 버전 검사

**이번 직접 검사:** 같은 authored HDL 15개를 별도 `syntax2024` 폴더에서 `C:\Xilinx\Vivado\2024.2\bin\xvhdl.bat`/`xvlog.bat`로 분석했다. 둘 다 exit0. [명령 기록](<감사 폴더>/toolcheck/syntax2024/COMMANDS.txt), [VHDL 로그](<감사 폴더>/toolcheck/syntax2024/xvhdl.log), [Verilog 로그](<감사 폴더>/toolcheck/syntax2024/xvlog.log). 2020.2 결과를 이름만 바꾼 것이 아니라 별도 실행 결과다.

동일한 target/board 질의를 2024.2 batch로 실행했고 exit0이었다. `xc7z020clg400-1`은 반환되었고 `*zybo*z7*20*` board preset은 기본 catalog에서 미발견이었다. [2024.2 device query](<감사 폴더>/toolcheck/device_query_2024_2.log).

raw13에 필요한8개 + PCM 변환1개의 **XCI만 복사**해 `create_project -in_memory -part xc7z020clg400-1`, `read_ip`, `report_ip_status`를 수행했다. 최종 실행 exit0, 읽은 IP9개, 모두 `IS_LOCKED=1`이었다.

| IP | 2024.2 보고서 상태 | 보고서 권고 |
|---|---|---|
| fft_block | minor version change:9.0 Rev10→9.1 Rev13; part change | Upgrade IP |
| 나머지 Floating Point8개 | revision change:7.1 Rev2→7.1 Rev19; part change | 모두 Upgrade IP |

모든 original part는 `xc7k325tffg900-2`, 현재 in-memory target은 `xc7z020clg400-1`이다. `New License=Included`는 catalog 보고 값이며 IP 생성·합성 라이선스 실행 검증을 대신하지 않는다. FFT read 시 `[IP_Flow 19-6920]`으로 구버전 catalog definition/instance XML 및 port/interface 정보 부재 경고가 있었다. 최초 평면 복사에서 생긴 shared-output-directory 경고는 감사 폴더 배치 문제였으며, XCI별 하위 폴더로 재검사한 최종 보고서에서 제거되었다. read 오류는 없었고 복사 XCI9개의 hash가 원본과 동일함을 재확인했다.

최종 근거: [Tcl 검사 스크립트](<감사 폴더>/toolcheck/ip2024/query_ip_status_isolated.tcl), [실행 로그](<감사 폴더>/toolcheck/ip2024/ip_status_isolated_query_2024_2.log), [IP status 보고서](<감사 폴더>/toolcheck/ip2024/ip_status_isolated_2024_2.txt).

**구문 분석·XCI 상태 확인까지만 완료했다.** upgrade_ip, generate_target, mixed-language elaboration, 수치 simulation, synthesis/implementation, bitstream/보드 실행은 하지 않았다. 후속 작업에서는 업그레이드 전후 XCI/port/latency를 비교하고 전체 회귀 시험을 해야 한다.

## 9. 기존 FFT 재사용: 잠정 판정

FFT 재사용 검토 파일의 경로는 `docs/reviews/FFT_REUSE_REVIEW.md`이다. 현재 없으며 기다리지 않고 감사를 진행했다. 그 파일을 생성·수정하지 않는다.

| 항목 | 소스에서 확인 | 판정 |
|---|---|---|
| 입출력 | `F/rtl/top/fft_stream_top.sv:18–29`: signed16 complex, index/last/overflow | MFCC real 입력은 imag0. PCM/pre-emphasis 범위와 fixed scale 어댑터 필요 |
| 크기 | 같은 파일:66–67, cfg_log2_n3…10 | N512 설정 가능이라는 코드 사실. 실제 N512 검증 성공은 미확인 |
| 순서 | core 뒤 `natural_reorder_pingpong` 연결(:107–135) | raw core bit reverse와 wrapper natural order 구분 |
| 수치 | `F/rtl/r22sdf/r22sdf_runtime_group_quantize.sv:96–106,165–173`; `fixed_round_shift.sv` | twiddle shift15, group shift2, ties-to-even, wrap+overflow. N512 전체 S 및 stage 경로 미검증 |
| reset | `fft_stream_top.sv:138–144` | posedge clk active-low synchronous reset |
| backpressure | `fft_stream_top.sv:72–75`; `fft_axi_stream_wrapper.sv:83–86,158–159` | output-ready를 코어로 전달하지 않고 stall 오류만 기록. 임의 output stall 지원 아님 |
| 과거 시험 | `F/reports/simulation/phase6_xsim_summary.csv` | 6개 scenario/8frames, N16·N1024 PASS 기록 존재. N512 full-path 행 없음. 이번 재실행 아님 |
| 과거 AXIS | `phase8_axis_summary.csv` | N16 no-stall PASS 및 오류 검출 시험 기록; 보드 DMA 실행 근거 아님 |

**잠정 판단: 연결·배율 보정 후 재사용 후보이며 통합 승인 전이다.** 별도 검토와 N512 directed/연속/단독 drain·순서·스케일·overflow 시험을 통과해야 한다. 외부 backpressure가 가능하면 프레임 버퍼 또는 검증한 stall 구조가 필요하다. 원본 README의 Q1.15를 pre-emphasis 이후 값에 무조건 적용하지 않고 MFCC_SPEC의 /2 headroom·scale 복원을 검토한다.

기존 README의 NMSE, 105MHz 통과, LUT/FF/BRAM/DSP 수 및 2020.2 구현 성공은 **기존 문서의 주장/보관 결과**다. 이번 MFCC·2024.2·실보드의 성능으로 옮겨 쓰지 않는다. `F/reports/phase8_integration_status.md`는 XSA/bitstream/BSP/ELF/BOOT.bin 및 실제 board DMA 미완성을 명시한다. 이전 `vitis/src/main.c`도 ARM FFT/MFCC 연산 기준 모델이 아니라 DMA 시험 앱이다.

## 10. 검증 수준과 남은 결정

| 계층 | 이번 상태 |
|---|---|
| 출처 기록·보관본 대조 | GitHub520 + FFT선택23 hash 일치, source snapshot 식별 |
| 계수 | Hamming/Mel/DCT/scale/lifter 수식의 binary32 상수 비교 통과 |
| 소스 분석 | 모듈 연결·IP·clock/reset 위험 확인 |
| 도구 | 2020.2/2024.2 각각 HDL 분석·part 질의 성공. 2024.2 XCI9개 상태 보고 성공, 모두 locked/upgrade 필요 |
| Python/C MFCC | 새 프로젝트 구현·공통 PCM 출력 대조 아직 없음 |
| IP/custom MFCC sim | 전체 elaboration·수치 시뮬레이션 미실행 |
| 합성/구현/보드 | 이번 MFCC 결과 없음. timing·자원·전력·정확도 수치 없음 |

핵심 불확실성은 (1) 원본 pre-emphasis와 framing의 실제 시간 동작, (2) FFT 실제 이득/연속 프레임 경계, (3) 2024.2 IP upgrade 후 latency·timing·자원, (4) 기존 FFT N512/고정소수점 log 정밀도, (5) 신뢰할 PS7 DDR/MIO 설정과 보드 실행 환경, (6) 확보한 공통 PCM의 각 구현 입력·결과 연결 검증, (7) 외부 코드 사용 조건이다.

## 11. 제한 일정의 우선 경로와 후속 범위

**설계 제안 — 가장 먼저 완성할 경로:** 확보한 음성 파일의 공통 PCM → Python raw13 기준 → host C 단계 대조 → PS 메모리에 저장한 동일 PCM의 ARM C 실행 → 계수 회수. 하드웨어 의존성을 기다리기 전에 프레임·스케일·수치 기준을 확립할 수 있다. 그 다음 저장 PCM→실제 PL raw13→PS 회수 경로를 만든다. 개발용 음성 1개와 합성 입력으로 먼저 검증하고, 조건을 고정한 뒤 별도 평가용 20개에 적용한다. 상세 범위는 [NEXT_TASK_PYTHON.md](NEXT_TASK_PYTHON.md)를 따른다.

| 시점 | 우선 산출물/판정 기준 | 완료를 가정하지 않을 항목 |
|---|---|---|
| 10월4일 | 이번 두 문서, 소스/도구 증거, profile 선택 | 구현 완료·성능 우위 |
| 10월5일 우선 | Python 전체 경로→C 같은 입력/프레임·13계수 검증; 확보된 공통 음성 manifest의 hash 사용. 보드 플랫폼 준비 병행 | IP HW 전체 완성 보장 없음 |
| 10월6일 교수님 전달 목표 | 실제 도달한 Python/C/ARM 단계와 재현 방법·오차, HW 진행/미완료 표를 동결 | 미완성 비교군의 성능 숫자·전력·인식 정확도 채워 넣지 않음 |
| 전달받은 10월7일11:00 KST 일정 전 | 재현 확인 및 결함 수정·산출물 정리 | 네 비교군 동시 완료 약속으로 해석하지 않음 |

후속 순서는 IP 기반 raw13의 2024.2 재생성·stage simulation→ZYBO buffer 입출력→기존 FFT 검토/N512 검증→fixed bit model과 Mel/log/DCT→두 HW를 같은 도구·소자·입력으로 측정이다. 부동소수점과 고정소수점의 다른 규격 결과를 한 비교표에 합치지 않는다.

다음 작업의 파일 범위는 MFCC_SPEC 8절에 명시했다. 이번에는 그 구현 파일을 만들지 않았다. 측정은 파일 읽기/공통 변환/초기 적재, ARM 연산, HW core, PS 메모리→전송/cache/control→결과 회수, 시각화를 나눠 기록한다. 학교·논문·발표 자료는 개발 저장소 밖에 유지한다.
