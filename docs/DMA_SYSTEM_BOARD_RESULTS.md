# Fixed/FP32 MFCC DMA·인터럽트 통합 및 보드 검증

작성일: 2026-10-05 KST. **Fixed·FP32 모두 실제 보드 실행 및 독립 감사 완료.**
증거 루트: `D:/2610_MFCC/build/board_validation/dma_board_20261005_01`.

## 최신 작업 범위

사용자가 FP32와 fixed 양쪽을 DMA + 인터럽트로 변경하고 보드에서 다시
실행하도록 지시했다. 전달한 채팅의 실제 사용자 메시지와 앞선 DMA/IRQ
대화를 `read_thread`로 확인했다. 이전 FP32 AXI-Lite polling 통합 후보는
bitstream 구현 전에 중단하고 그 검사 결과를 보존했다.
기존 fixed s06/a07 보드 실행과 이전 C 측정도 유지한다.

ARM은 AXI4-Lite 제어만 담당하고, PCM과 결과 데이터는 AXI DMA가 DDR과
MFCC 사이에서 전송한다. MFCC 연산은 각 bitstream의 동결된 RTL/IP에서
실행한다. DMA MM2S/S2MM 및 코어 완료·오류 인터럽트를 GIC로 연결하고,
ARM은 WFI로 기다린다. 입력 DMA 완료만으로 연산 완료를 선언하지 않는다.

## 실제 보드 결과

ZYBO Z7-20 JTAG serial `210351B40030A`, UART COM7에서 실행했다.
FPGA 구성·동일 XSA의 PS 초기화·ELF 부팅 후 smoke512samples, 개발 음성 전체,
같은 ELF의3warmup+30측정을 순서대로 수행한다. 단계 사이 재프로그램이나
ELF 재다운로드가 없으며, 실제 CPU PC와 raw 실행 환경을 매 단계 기록한다.

| 항목 | Fixed DMA/IRQ | FP32 DMA/IRQ |
|---|---|---|
| 시스템 / ARM / 보드 실행 | i10 / i01 / fixed_03 | f06 / p01 / fp32_01 |
| 개발 입력 | 85920samples / 534frames / 6942records | 동일 |
| 동결 RTL raw·metadata 일치 | 전부 일치 | 전부 일치 |
| 예열3회 + 측정30회 출력 | 33회 전부 검증 | 33회 전부 검증 |
| 측정 중앙값 | 124.823717ms | 275.292867ms |
| 최소 / p95 / 최대 | 124.821833 / 124.824779 / 124.824848ms | 275.290888 / 275.294107 / 275.294407ms |
| PL BUSY 중앙값 | 123.780281ms | 274.249463ms |
| WFI 전후 측정 구간 중앙값 | 123.778394ms | 274.247496ms |
| 매회 IRQ TX / RX / core / timeout | 1 / 1 / 1 / 0 | 1 / 1 / 1 / 0 |
| 매회 WFI 실행 횟수 | 2 | 2 |
| Python float64 수치 판정 | NOT_ACCEPTED 유지 | 이번 개발 음성 허용치 통과 |

Fixed 결과 SHA256은
`888ee289cc44f03602934002e9c4e2793cd3c4bc6349547c247bbda934eba6a6`이며
기존 fixed polling RTL 결과와 같다. Python float64 대비 최대 절대 오차는
0.05517150245522062, RMSE는0.006260244715270819다. 전송·raw 일치의
성공이 fixed 수치 정확도 허용치 통과를 뜻하지 않는다.

Fixed bit SHA256:
`9f8355908341f35d508f8b6932f35b2c52bfcedeecdcfd9fddaa128f25e1346e`.
XSA SHA256:
`8eaebc0c0351dcc1c7b1b7fe17c182cd3e6e960f956d7d1e3575e167601c8331`.
ELF SHA256:
`3c3eb77d4aa35598d80805092579ae03716ec53b8867f52b057a60cc9b17d379`.
`independent_analysis/fixed_03_audit.json`의 독립 감사도 PASS했다.
274개 indexed artifact와 실제 ELF/XSA, raw 상태·반복 기록·33회 전체 결과,
동일 ELF 세션, 제한된 debugger loadhw, MMU/cache/GIC/timeout cleanup을
검사했다. 실제 설정 레지스터로 계산한 FCLK는99,999,999Hz이며 발진기를
계측한 값은 아니다. Fixed float64 허용치 위반은4177/6942개로 유지한다.

FP32 결과 SHA256은
`f6e8fa45d75d518ba62771a79e32c11c5e8279a8a503f59da2aab8efa0237ba3`이다.
동결 RTL/IP 시뮬레이션의 IEEE754 raw32 비트와 모든 메타데이터가 일치했다.
Python float64 대비 최대 절대 오차0.00027009753311091345,
RMSE2.7910545339234544e-5이며 PC C 대비 최대 오차0.000270843505859375,
RMSE2.7653654441061345e-5다. 기존 허용치 `1e-3 + 1e-5*abs(reference)`에서
두 기준 모두 위반0개, 비유한 값0개다. 기존 합성 입력 실패5개는 재시험하지
않았으며 그대로 남는다.

FP32 bit SHA256:
`523616afea6d24aff28c26510b18571f427b9b237fb9ae72178ed5809b633f0e`.
XSA SHA256:
`6e43d3077e39ab0f7c755cccefd1962fd01147daf75653a597e72d921d23539b`.
ELF SHA256:
`9a4abde7fa6ff275bc512639057104f95bd428063b8ced23312678029ea8885e`.
`independent_analysis/fp32_01_audit.json`도274개 indexed artifact, 전체 개발
출력과33회 이력, raw 실행 환경과 같은-ELF 반복을 독립 검사해 PASS했다.
독립 계산의 FP32 Python RMSE는2.7910545339234584e-5로, 마지막 자릿수의
합산 차이만 있으며 허용치 판정과 최대 오차는 동일하다.

두 ELF의 실제 버퍼·상태·layout·breakpoint 주소는 모두 같고, layout의
유일한 값 차이는 CORE_ID1/2다. PS 초기화 파일 해시, BSP CPU/timer 주파수,
DMA HWH 설정, 측정 구간도 동일함을 교차 확인했다. 공통 전송 RTL 해시는
`40037009ac13c2eee2ddc1d63ec4c52d2aa1467efced878022fbf7401aec045c`다.

두 시스템의 최종 배치배선 결과는 다음과 같다. 각 bitstream에는 해당 MFCC
코어1개만 들어가며, 아래 자원은 PS 주변 AXI DMA·연결·전송 제어를 포함한다.

| 시스템 | 100MHz setup / hold 여유 | LUT | FF | BRAM tiles | DSP |
|---|---|---:|---:|---:|---:|
| Fixed i10 | +0.447 / +0.010ns | 9325 | 6843 | 15.5 | 40 |
| FP32 f06 | +0.846 / +0.007ns | 8302 | 12266 | 11 | 26 |

두 시스템 모두 setup/hold 실패 endpoint, 내부 미제약 경로, clock 없는
레지스터, 조합·latch loop는0이다. 위 값은 standalone 코어 OOC 합성 자원과
다른 범위다. FP32 DSP는 합성28개에서 구현26개로 최적화됐으며, 전송 제어와
adapter에는 DSP가 없다. 벤더 primitive 배치·DSP/BRAM 사용 관련 경고는
각 실행의 DRC 보고서에 보존하며, DRC 오류는 없다.

기존 기록은 다음과 같이 보존한다. 아래 ARM C와 polling 시간은 초기화·
캐시 처리·출력 저장 및 반복 정책이 새 DMA 측정과 달라, 같은 구간의
speedup 비율을 계산하는 기준으로 사용하지 않는다.

| 기존 경로 | MFCC 연산 위치 / ARM 역할 | 기록된 중앙값 |
|---|---|---:|
| FP32 C float_03 | ARM에서 C 연산 | 127.434356ms |
| Fixed C fixed_01 (이전 증거 루트) | ARM에서 C 연산 | 1529.340455ms |
| Fixed RTL polling accel_03 | FPGA RTL 연산 / ARM polling·데이터 전달 | 123.965773ms |

이전 FP32 상태는 RTL/IP 코어 검증까지였고 보드 통합·실측은 완료되지
않았었다. 이번 기록은 DMA/IRQ 통합과 실제 FPGA 실행 증거를 추가한다.

## 공통 전송·측정 조건

상세 규격은 [DMA 계약](../hardware/system_dma/REGISTER_CONTRACT.md)에 있다.

| 항목 | 공통 조건 |
|---|---|
| 보드·도구 | ZYBO Z7-20, Vivado/Vitis2024.2, bare-metal CPU0 |
| 제어·메모리 | GP0 제어, HP0 비일관성 DDR 경로 |
| DMA | Simple/noSG, memory64bit, stream32bit, burst16, DRE, length23bit |
| PL clock | FCLK0 목표100MHz; 배치배선과 실제 레지스터 확인은 별도 |
| 입력 | 동일 PCM16 LE, 한32bit beat에2samples, 홀수 마지막 TKEEP0x3 |
| 결과 | 한 계수당24bytes: raw64/frame32/index32/BFP32/flags32 |
| 메타데이터 | 전부 DDR 레코드 안에 저장; TUSER에만 맡기지 않음 |
| 출력 TLAST | 전체 클립의 마지막 레코드 마지막 word에만1 |
| 개발 음성 | 85920samples/534frames/6942records |
| 전송량 | 입력171840bytes, 출력166608bytes, 두 구현 동일 |
| 반복 정책 | 같은 ELF 안에서3warmup+30측정, 매회 같은 버퍼/캐시 정책 |
| 측정 구간 | DDR 입력 준비 뒤 설정·캐시 관리부터 DMA/코어 완료와 출력 invalidate까지 |
| 구간 밖 | FPGA/ELF startup, JTAG/UART, 결과 history 복사 및 host 수치 비교 |

FP32는 원본 binary32 비트를 유지하고 high32/BFP는0이다. Fixed는 signed40
Q24 값을 signed64로 부호 확장하고 BFP 진단 지수도 보존한다. BFP를 최종
Q24 결과에 다시 곱하지 않는다. 산술·계수·프레이밍·허용치는 변경하지 않는다.

시간 측정 중 CPU가 실제로 WFI를 수행했는지 IRQ/wake 기록으로 확인한다.
WFI 전후 tick이나 PL busy tick을 CPU 점유율 또는 순수 연산시간으로
바꾸어 해석하지 않는다. PL busy에는 reset/recovery·DMA backpressure와
직렬화 비용이 들어간다. 숫자로 제시할 CPU 점유 시간은 아직 측정하지 않았다.

## 완료된 준비 검사

- 새 작업 시작 시46개 동결 산출물/소스 해시가 이전 기준과 일치했다.
- `system_dma/p01`은 DMA32/64bit, DRE, burst16, length23 설정을 Vivado가
  수락함을 확인한 메타데이터 검사다. 연결 전 GP0/HP0 주파수 속성은10MHz여서
  최종100MHz 구성 gate를 통과한 것으로 간주하지 않는다.
- 설정 검사기는872개 보호된 PS 속성을 기존 PS-only 기준과 비교한다.
  IRQ_F2P 연결에 필요한 입력 수16은 별도 필수 값으로 검사한다.
  단위 오류 주입 검사6개 그룹에서 DDR/MIO/CPU 변경, 잘못된 DMA 설정,
  주소 범위, IRQ 교차/누락, 잘못된 slave aperture 정의를 거부했다.
- `system_dma/f01`은 연결 검증 후 주소 보고서 해석 오류로 중단했다.
  Vivado의 OFFSET 없는 slave aperture 정의3행을 실제 매핑4행과 구별하고,
  IRQ_F2P 입력 수16을 명시적으로 검사하도록 감사 코드를 보완했다.
  같은 f01 보고서에 대한 읽기 전용 재검사는 PASS지만 원래 실패 기록을
  완료로 바꾸지 않으며, 수정된 검사기를 새 실행에 snapshot한다.
- `system_dma/unit05`는 fixed/FP32 형식 각각12개 전송 경계 검사군을
  통과했다. 각15258cycles, 입력 stall6130/출력 stall472/PCM stall4734를
  포함한다. 이 검사는 실제 산술 코어·DMA IP·DDR 또는 ARM ISR 실행이 아니다.
- `system_dma/f03`의 연결 후 설정 감사는 PASS다. GP0/HP0/FCLK100MHz,
  DMA memory64/stream32bit, 실제 코어 선택2(FP32), 주소4개와 IRQ 연결을
  확인했다. 시뮬레이션은914.4CPU초 동안 결과나 단계 진행을 확인할 수 없어
  해당 실행의 simulator만 중단했다. `diagnostic_stop.json`의 판정은
  `INCOMPLETE_DIAGNOSTIC_CPU_CUTOFF`이며 기능 assertion 실패나 통과를
  의미하지 않는다. 결과 JSON과 성공 sentinel이 없어 구현 gate는 열지 않았다.
  단계별 즉시 출력과 독립 시간 제한을 보강한 fixed `i02`에서 PS FCLK0
  상승 에지 관측 수0인 상태로20ms simulation-time watchdog이 발생했다.
  첫 GP0 접근 전이며 MFCC 산술/DMA 수치 실패 판정은 아니다.
  기존 통과한 PS 모델 실행과의 차이인 `xelab --O3`를 제거하고,10us 안에
  클록 부재를 감지하는 `i03`에서 기본 최적화를 시험했으나 같은 문제가
  남았다. 내부 generator는1/half-period5ns인데 PS 출력과 공통 clocknet은X였다.
  별도 `clock01`에서 같은 컴파일 소스를 debug-all로 다시 elaboration하자
  PS·공통net·DMA 클록이 정상 값으로 관측되고, 각 net의 driver는 설치된
  PS VIP의 assign 하나로 확인됐다. 최적화 옵션만의 문제라는 가설은
  기각한다. 관측된 설정 차이를 기록하며 시뮬레이터 내부 원인은 단정하지 않는다.
  같은 진단 snapshot에서105ns clock1/11cycles,2000ns clock0/200cycles를
  확인했다.30us 실행에서 첫 GP0 read(234cycles), 식별 레지스터, 잘못된
  1byte DMA 입력,173samples 소비 후 DMA reset/core ABORT, 빈 클립을
  통과했다. 클록 강제 구동은 없으며, 최종 전체 검증은 새 fixed `i04`에서 진행한다.
- `i04`의 Vivado debug-all 실행에서도 클록과 앞선 복구 검사가 정상 동작했다.
  빈 클립과511sample은 통과했고,512sample의78word 스트림·카운터·IRQ 결합·
  S2MM_LENGTH312 검사까지 통과했다. 이후 DDR 직접 읽기에서 offset256의
  값이 초기값CCCCCCCC로 남아 실패했다(345175ns). 마지막56byte 쓰기와
  PS VIP DDR 반영 시점을 추적 중이며, 출력 전체 통과나 구현 완료로 기록하지 않는다.
  별도 `ddr01` 파형에서 해당 HP 버스트의 주소·7개64bit 데이터·WSTRB·WLAST·
  BRESP는 정상이고 DDR 요청의 유효56byte 데이터도 정상임을 확인했다.
  DDR ACK가 IRQ/검사보다 앞서므로 단순히 대기 시간을 늘릴 근거는 없다.
  `ddr02`에서는 DDR 요청의 현재 주소와 메모리 write task가 소비한 주소가
  한 버스트씩 어긋나는 현상을 관측했다. 설치된 모델 원본의 재컴파일로
  재현성을 확인 중이다. 초기 파형 해석에서 사용하지 않는 상위 X 비트를
  0으로 처리한 오류는 수정했으며, 데이터가0으로 변했다는 주장은 기각했다.
  설치된 PS VIP의4port write arbiter는 같은 clock edge에서 request를
  blocking assignment로 먼저 올리고, `#0` 뒤 payload/address를 갱신한다.
  DDR consumer가 그 사이 실행되면 이전 burst를 쓰는 race가 있다.
  최적화 O0의 `ddr03`에서도 같은 실패가 재현됐다.
  검증용 모델 복사본은 해당 모듈의 request assignment22개만 nonblocking으로
  바꾸고, 원본·수정본·diff 해시를 보존한다. 독립 검토에서 다른8개 HDL 파일과
  나머지 원본 바이트는 동일함을 확인했다. 설치 HDL 원본과 합성·구현 소스는
  변경하지 않는다. `ddr04`의 첫 컴파일은 논리 library가 설치된 precompiled
  cache로 연결되어 해당 cache를 갱신한 문제가 독립 검토에서 발견됐다.
  이 진단은 최종 검증으로 채택하지 않으며, cache를 원본 모델로 복구하고
  작업 디렉터리의 전용 library로 분리하는 조치를 진행한다.
  공용 cache 복구는 자동 승인 검토에서 명시적 사용자 승인이 필요하다는
  이유로 거부됐다. 변경된 cache와 복구 명령은 `cache_incident01`에 보존했고,
  사용자에게 승인을 요청했다. 복구 승인 대기 중에도 전용 local library와
  설치 cache 변경 감지 검사를 사용하는 작업은 계속한다.
  `ddr04` 파형에서는 수정된 request scheduling으로 현재 burst의 주소·길이가
  memory task에 전달되며 이전 fatal 지점을 통과했지만, cache 격리 문제 때문에
  이 진단 자체를 최종 통합 PASS로 채택하지 않는다.
  이후 통합 시뮬레이션은 이 scheduling 수정이 적용된
  vendor PS 모델을 사용한다는 조건을 명시하며, 원본 모델 통과로 표현하지 않는다.
- `arm_dma/host_fixed_01`, `host_fp32_04`는 같은 ARM 소스3개를 기존 BSP와
  설치된 드라이버 헤더로 컴파일하고, 각각8,387,676개 portable 검사를
  통과했다. 입력 길이0…262144, 코어 식별, 완료 결합, 오류·복구 및 timer
  원점/overflow를 포함한다. 최종 XSA로 만든 BSP/ELF 또는 보드 증거는 아니다.
- `runner_tests_05`는 보드 접근 없이437개 구조체·비교·오류 주입·실행 순서
  검사를 통과했다. 중간 반복 결과 손상, IRQ/전송 길이 오류, 잘못된 JTAG
  선택/초기화 및 GIC·timer·cache 실행 환경 오류를 거부하는지 확인했다.
  준비 snapshot은 증거 루트의 `preparation_01`에 보존했다.
- 독립 검토 `independent_analysis/arm_review_02.json`은 준비 snapshot과
  현재 소스 해시가 일치하고 추가 소스 차단 문제가 없음을 확인했다.
  별도의 결과 감사기는 runner 비교 코드를 가져오지 않고 raw 레코드,
  실제 ELF/XSA, CPU·GIC·timer 레지스터와33회 출력을 검사하도록 준비했다.
- `system_dma/i08` fixed 통합 시뮬레이션은 로컬 PS 모델 scheduling 수정과
  실제 AXI DMA IP를 사용해 PASS했다.0/511/512/671/672/672sample 총6회,
  3038samples/6frames/78records의 모든 DDR word가 일치했고 canary6회도
  정상이다. MM2S/S2MM/core IRQ 수는5/4/6, AXI read/write 수는191/117,
  clock10ns·총170518cycles였다. 잘못된 입력 복구와173samples 중간 ABORT,
  input stall·held stream24476cycles도 검사했다. 이 실행의 output stall은0이며
  출력 backpressure는 별도 `unit05`에서 검사했다. 로컬 모델 컴파일 파일26개와
  전용 library 연결을 확인했고, 설치 cache 해시는 실행 전후 동일하다.
  ARM 명령 실행이나 보드 결과를 뜻하지 않는다. 합성 계층은 fixed 코어1개,
  FP32 코어0개를 확인했다. 이후 구현에서 출력 레코드 개수의 조합 계산
  `(N-512)/160*13` 경로가 타이밍을 위반했다. 합성 WNS -9.157ns,
  배치배선 WNS -10.599ns / hold slack +0.020ns로 bit/XSA gate를 통과하지
  못했으며 실패 보고서와 routed DCP를 보존한다. 이 제어 계산을 native START
  전의 bounded preparation state로 나누는 수정을 진행한다. 두 변형에 같은
  준비 과정을 사용하고 BUSY 시간에 포함하며, MFCC 산술·출력 ABI는 유지한다.
- `system_dma/unit06`은 준비 단계 수정 후 fixed/FP32 형식 각각15개 검사군을
  통과했다(각23340cycles).0/1/511/512/671/672/831/832/85920/262144samples의
  출력 개수와 준비 시간을 검사하고, 준비 중 ABORT·재시작, 잘못된 제어 명령,
  오류 상태의 W1C 처리도 확인했다. 개발 입력은534 준비 클럭(100MHz에서5.34us),
  최대 입력은1636클럭이다. 전송 RTL 해시는
  `bdca4e3636c44a7e762ab80b31d7ccc6293b3a3c95648efedf7b8ab2a8a2a18f`이며
  이 검사는 전체 시스템의 타이밍 완료를 의미하지 않는다.
- `system_dma/i09`은 준비 단계 수정본으로 실제 DMA 통합 시뮬레이션을 다시
  통과했다.6회/3038samples/6frames/78records와 DDR canary6회, IRQ·잘못된
  입력 복구·중간 ABORT가 모두 통과했다. 로컬 library 및 설치 cache 해시
  검사도 통과했다. 작은 입력의 준비 계산은 GP0의 DMA 설정 중 끝나 전체
  시뮬레이션 cycle 수는 i08과 같았으며, 이를 개발 입력의 준비 시간이0이라는
  뜻으로 해석하지 않는다. 수정본의 배치배선은 WNS -0.322ns / TNS -1.319ns,
  setup 위반11개·hold slack +0.020ns로 실패했다. 긴 출력 개수 나눗셈 경로는
  제거됐으나 입력 마지막 beat 판정에서 오류·완료 상태 결정까지 이어지는
  제어 경로가 남았다. 완료 확인을 등록된 카운터와 별도 단계로 분리하는
  수정을 진행하며, 이 실행의 bit/XSA는 생성하지 않는다.
- `system_dma/unit07`은 별도 FINISH 단계 수정본으로 두 형식 각각18개 검사군을
  통과했다(각26411cycles, input/output/PCM stall7497/557/5760). 준비 단계와
  기존 경계 검사를 유지하며, 조기 DONE 중 출력 stall, 마지막 native record와
  DONE의 동시 발생, 마지막 출력 word 뒤 완료 지연, 늦은 native/MMIO 오류의
  성공보다 높은 우선순위, DONE/W1C 동시 처리를 확인했다. 전송 RTL 해시는
  `40037009ac13c2eee2ddc1d63ec4c52d2aa1467efced878022fbf7401aec045c`다.
  등록된 native DONE 뒤에는 추가 PCM/native record를 받지 않고, 기존 출력
  레코드를 배출한 뒤 등록된 카운터를 검사한다. FINISH 시간도 BUSY 및 두 변형의
  보드 측정 구간에 포함하며, 전체 시스템 타이밍은 새 실행에서 확인한다.
- `system_dma/i10`의 실제 DMA/PS DDR 통합 시뮬레이션은 최종 PREP+FINISH
  수정본으로 통과했다.6회/3038samples/6frames/78records의 DDR word 불일치0,
  canary6회, MM2S/S2MM/core IRQ5/4/6, 잘못된 입력 복구와173samples 중간
  ABORT를 확인했다. 총170525cycles로 i09보다 검사 전체에서7클럭 늘었고,
  clock10ns·vendor reset 경고0·전용 library/설치 cache 보존 검사도 통과했다.
  합성 WNS +0.956ns, 최종 배치배선 WNS +0.447ns / hold slack +0.010ns로
  100MHz timing을 통과했다. Setup/hold 위반 경로는 모두0개다.
  bit/XSA도 생성 완료했다. 전체 사용량은9325LUT/6843FF/15.5BRAM/40DSP,
  transport는988LUT/600FF이며 MFCC 산술 소스는 동결본과 동일하다.
- `arm_dma/i01`은 성공한 i10 XSA로 BSP/ELF를 새로 생성했다. 실제 BSP에서
  MM2S/S2MM/core IRQ61/62/63과 timer29를 확인했고 `prepare_fixed_i01`의
  오프라인 artifact/layout/reference 검사를 통과했다. 준비 시 runner437개
  기존 검사와 simulation-model binding17개 오류 주입 검사도 통과했다.
- 물리 보드 `fixed_01`은 i10 bitstream/i01 ELF를 올리고 UART
  `ARM_DMA_READY_V2 core=1` 및 READY status(state1/result0)를 확인했다.
  이후 host XSCT가0x40400000의 DMA 상태를 읽을 때 새 PL 주소가 디버거
  memory map에 등록되지 않았다는 이유로 거부했다. DMA 실행·수치·시간
  판정 전의 실패이며 원래 실패 폴더와 로그를 보존한다. Manifest의
  `board_executed=false`는 성공한 clip job이 없다는 뜻으로, 실제 프로그래밍과
  ELF 부팅이 없었다는 뜻은 아니다. XSA에서 검증한 두64KiB MMIO 영역만
  디버거에 등록하도록 실행 도구를 보강한 뒤 새 실행 ID로 재검증한다.
- `fixed_02`도 READY까지 도달했으나 같은 debugger 접근 거부로 중단됐다.
  `memmap -list`에는 두 영역이 실제로 등록돼 있었으므로 단순 등록 누락
  가설은 기각했다. 설치 XSCT 소스는 일반 `memmap`을 CPU context에,
  ARM용 `loadhw`는 최상위 parent context에 적용함을 확인했다. 별도
  `debug_map_01`은 동일 i10 XSA에서 두 MMIO 범위만 `loadhw -mem-ranges`로
  연결한 뒤 DMA/core 상태 읽기에 성공했다. Core ID/ABI는4d464343/00020000,
  전후 PC는001018a8로 같은 READY 위치다. 이 진단에는 reset·program·ELF
  download·continue·물리 메모리 쓰기가 없었다. 실제 DMA 결과는 아직 아니다.
- `system_dma/f06`은 같은 최종 전송 RTL과 실제 FP32 IP를 연결한 DMA/PS DDR
  통합 시뮬레이션을 통과했다.6회/3038samples/6frames/78records의 raw IEEE
  payload·메타데이터·DDR word 불일치는0개다. Canaries6회, IRQ5/4/6, 잘못된
  입력 복구와173sample 중간 ABORT·동일 입력 재시작도 통과했다. 총414678cycles,
  clock10ns, input held/stall136403cycles이며 이 실행의 output stall은0이다.
  출력 backpressure는 별도 unit07에서 검사한다. Native reset LOW16이상/
  HIGH4이상 assertion과 vendor reset 경고0, local model/설치 cache 보존
  검사도 통과했다. 합성 WNS +1.576ns, 최종 배치배선 WNS +0.846ns /
  hold slack +0.007ns로100MHz timing도 통과했고 setup/hold 위반은0개다.
  bit/XSA 생성도 완료했다. 같은 XSA로 p01 BSP/ELF를 생성하고, 최종
  scoped-loadhw runner로 fp32_01 보드 실행·33회 출력 및 독립 감사까지
  완료했다. 상세 수치와 시간은 문서 앞의 실제 보드 결과에 기록했다.

실제 DMA/PS DDR 모델, 전체 시스템 합성·배치배선, bit/XSA, ARM ELF와
물리 보드 수치/IRQ/시간 검증은 fixed와 FP32 모두 완료했다.
최종 기계 판독 요약과46개 동결 파일 보존 검사는 증거 루트의
`summary.json`, `preservation_final.json`에 기록한다.

## 수치 판정과 보존

통신 성공, 동결 코어의 raw/metadata 비트 일치, Python float64 허용치
판정을 구분한다. Fixed의 기존 float64 정확도 `NOT_ACCEPTED`는 유지한다.
FP32의 기존 합성 입력 실패5개도 유지하며, 이번 개발 음성만으로 전체
합성/평가 음성이 통과했다고 쓰지 않는다. 평가 음성20개는 실행하지 않는다.

이전 세 경로의 수치와 polling 시간은
[이전 보드 보고서](BOARD_VALIDATION_RESULTS.md)에 보존한다.
FP32 polling 후보의 reset 경고·보강 이력은
[FP32 후보 기록](FP32_SYSTEM_BOARD_RESULTS.md)에 있다.
DMA 방식의 같은-ELF warmup/캐시 포함 시간과 이전 polling의 매회 새 초기화/
캐시 제외 시간을 동일 구간의 speedup으로 곧바로 비교하지 않는다.

Flash/SD 영구 기록과 커밋·푸시는 하지 않았다. 이전 동결 검증 자료와
실패 실행 증거를 보존했다. 주 에이전트만 JTAG/UART를 사용했으며,
최종 상태는 FP32 RESULT breakpoint에서 CPU0 halt, CPU1 halt다.
UART capture는 종료했고 기존 hw_server는 유지한다.

## 별도 승인 대기: 공용 시뮬레이션 캐시 복구

`ddr04` 진단 중 설치된 공용 PS VIP compiled cache가 갱신됐다.
설치 HDL 원본은 변경되지 않았으며, 변경된 cache와 inventory·복구 스크립트는
`D:/2610_MFCC/build/system_dma/cache_incident01`에 보존했다. 원본 HDL로
해당 cache를 다시 컴파일하는 복구 명령은 자동 승인 검토에서 프로젝트 밖
공용 설치 환경 변경에 명시적 사용자 승인이 필요하다는 이유로 거부됐다.
사용자 승인을 요청했으며 아직 복구 명령을 실행하지 않았다.

이 공용 cache 변경은 다른 PS 시뮬레이션에 영향을 줄 수 있다. 최종 i10/f06은
각 실행 폴더의 별도 library를 사용했고 설치 cache가 실행 중 추가로 바뀌지
않았음을 검사했다. 공용 cache 복구 완료나 설치 환경 전체 무변경으로
표현하지 않는다. 원본 HDL 재컴파일은 원본 모델 동작으로의 복구이며,
백업이 없는 진단 이전 개별 compiled cache 바이트까지 같게 만드는 것은 아니다.
