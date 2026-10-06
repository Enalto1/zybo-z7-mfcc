# FP32 F0 프레임 버퍼 구조

작성: 2026-10-04. 대상: Vivado 2024.2 / `xc7z020clg400-1`.
이 문서는 RTL 작성 전에 정한 F0 구조·인터페이스 계약이다. F0는 데이터의
32비트 패턴을 보존하는 프레이머이며 부동소수점 계산이나 전체 MFCC 검증을
대신하지 않는다. 공통 정의는 `MFCC_SPEC.md`의 길이512, hop160, 무패딩이다.

## 원본에서 확인한 문제와 변경 근거

참고 `reference_code/github_mfcc/FPGA source/Calculation MFCC features/get_frames.vhd`
51–57행은 512/160과 512×32 배열 두 개를 선언한다. 73–75행은 매 유효 입력마다
전체 배열을 shift하고, 120–122행은 출력 배열 전체 복사/shift를 수행한다.
68–101행은 `clk_slow`/`clk_fast`를 사용하지만 프레임 이벤트와 배열 전달의 CDC
프로토콜이 없고, 36–46행 포트에 ready·EOF·클립 재시작 계약도 없다. 따라서
이번 교체 이유는 VHDL 언어 자체가 아니라 배열 이동, CDC, backpressure와
EOF 계약을 명시적으로 해결해야 하기 때문이다. 원본은 수정하지 않는다.

## 저장 방식과 처리 순서

한 개의 512×32비트 원형 이력 RAM을 사용한다. 첫512개 입력을 받은 후 입력을
정지하고 가장 오래된 샘플부터512개를 전송한다. 마지막 출력이 수락된 뒤
160개 새 입력만 받아 기존352개와 다음 프레임을 만든다. 출력이 진행되는
동안 RAM을 쓰지 않으므로 별도 snapshot/ping-pong RAM이 필요 없다.

쓰기 포인터는 다음에 덮어쓸 위치이다. 프레임을 완성하는 쓰기 직후의
쓰기 포인터가 해당 프레임의 oldest 주소이다. 읽기 주소는 수락된 출력마다
mod512로 증가한다. `frame_id`와 `start_sample`은 프레임 마지막 출력 수락
시에만 다음 값(+1,+160)으로 이동한다. 데이터는 연산 없이 비트 그대로 이동한다.

메모리는 AMD `xpm_memory_sdpram`, `MEMORY_PRIMITIVE="block"`,
`CLOCKING_MODE="common_clock"`, `READ_LATENCY_B=2`, `WRITE_MODE_B="no_change"`,
ECC OFF이다. 저장 공간은16,384bit이며 실제 BRAM 수는 합성 보고서로 확인한다.
읽기는 한 번에 하나만 요청하고, 2클록 RAM 지연 이후 wrapper의 hold register에
저장한다. 수락 전까지 새 읽기를 요청하지 않는다. 이는 성능 최적화 전의
직렬 기준 구조이며 수락 간격을 1클록이라고 주장하지 않는다.

UG953의 enb/read latency/regceb 계약에 따라 `regceb=1`을 유지하고 명시적
READ→WAIT→CAPTURE 상태로 wrapper capture 시점을 구분한다. `rstb=0`이며 RAM
내용과 메모리 출력은 reset하지 않는다. wrapper valid와 상태만 reset하여
초기/이전 클립 내용이 노출되지 않게 한다. 새로운 클립의 첫 프레임은 반드시
512개 새 샘플을 받은 뒤에만 만들어진다. [AMD UG953 2024.2 XPM_MEMORY_SDPRAM](https://docs.amd.com/r/2024.2-English/ug953-vivado-7series-libraries/XPM_MEMORY_SDPRAM).

## 외부 포트와 수락 계약

모든 포트는 하나의 `clk` 도메인이다. `rst_n`은 동기 active-low이며 최소 한
상승 에지에 낮게 유지한다. 데이터 폭은 이번 검증 범위에서32만 지원하며
`DATA_WIDTH!=32`는 elaboration에서 거부한다. 수치 해석은 호출자가 담당한다.

| 채널/포트 | 계약 |
|---|---|
| `clip_start_valid/ready` | IDLE에서만 수락. 프레임/샘플 카운터, 포인터, EOF, error를 초기화하고 첫512개 입력 수집 시작 |
| `s_valid/ready`, `s_data[DATA_WIDTH-1:0]` | 수락은 valid&&ready. 완성 프레임이 대기/전송 중이면 ready=0. source는 stall 동안 valid/data 유지 |
| `clip_end_valid/ready` | source는 마지막 입력 수락 이후 EOF를 제시. 활성 클립이고 EOF 미수락이며 같은 에지에 입력을 받지 않을 때 수락 가능. 읽기/출력 stall 중에도 수락 가능 |
| `m_valid/ready`, `m_data` | 출력 수락은 valid&&ready. stall 동안 valid/data와 모든 메타데이터 유지 |
| `m_sample_index[8:0]` | 프레임 내0…511 |
| `m_frame_id[31:0]`, `m_start_sample[31:0]` | 0부터 연속 증가 / 0,160,320,… |
| `m_last` | sample_index511의 valid 전송에만1 |
| `clip_done_valid/ready` | 완성된 마지막 프레임이 모두 수락된 뒤 valid 유지. 부분 꼬리는 버림. done 수락 후 IDLE로 복귀 |
| `error` | 카운터 범위 초과/불법 내부 상태를 검출하면1. 새 clip_start 또는 reset에서 해제. 일반 backpressure는 오류 아님 |

EOF는 아직 수락되지 않은 input을 취소하는 신호가 아니다. source는 EOF 이후
추가 input을 제시하지 않는다. 동시에 input과 EOF가 제시되면 input 수락을
우선하며 EOF ready는 낮춘다. 미완성 프레임은 내보내지 않으므로 0/1/511개의
입력은 출력0개이다. 512/671은1프레임, 672/831은2프레임, 832는3프레임이다.

reset은 미완료 input/프레임/출력/EOF/done을 취소한다. reset 후에는 새로운
clip_start가 필요하다. 불법 상태에서는 입출력을 무효화하고 error를 유지한
채 done 상태로 복구한다. 이 동작은 RTL 계약이며 물리적 SEU 복구 보장은 아니다.

## 검증과 현재 상태

검증 계획은 실제 vendor XPM 모델을 사용하는 xsim과 별도의 OOC 합성이다.
길이0,1,511,512,671,672,831,832 및 다회 wrap 장입, 입력 gap, 출력 랜덤 stall,
마지막 sample의 긴 stall, 읽기/출력 중 reset, 연속 클립을 transaction 기준으로
검사한다. 모든 출력의 데이터, index, frame/start ID, last 및 done 시점을 검사한다.
합성은100MHz clock 제약으로 BRAM/LUT/FF/latch 및 폭 경고를 기록한다. 이는
OOC 합성이며 전체 FP32 MFCC 구현·배치배선·보드 처리율 결과가 아니다.

위 구조·검증 계획을 먼저 작성한 뒤 RTL을 구현하고 아래 결과를 직접 확인했다.

## 직접 실행 결과 — F0 단독

최종 실행: `build/fp32_hw/f0_verified_01` (`f0_probe_01`도 보존).

```powershell
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B `
  'D:\2610_MFCC\project\scripts\run_fp32_f0.py' --run-id f0_verified_01
```

Vivado v2024.2 Build5239630의 실제 XPM behavioral simulation이 통과했다.
완료13클립과 reset으로 취소한3클립의 전달된 prefix를 포함해21,521개 출력
word를 비트 비교했다. 경계 길이8종, 최대4,512sample/26frame wrap, 연속 valid와
입력 gap, 출력 stall14,596cycle(마지막 word4,090cycle), 입력 backpressure81,445cycle,
done hold213cycle, 마지막 출력 stall 중 EOF 수락을 확인했다. 수치는 TB의
프로토콜 coverage 카운트이며 실제 음성 처리 시간이나 보드 성능이 아니다.

OOC 합성 결과는 **RAMB18E1 1개, RAMB36 0개, LUT177개, FF135개, latch0개**다.
RAM 저장 공간을16,384개의 FF로 만드는 shift-array 구조가 아닌 BRAM 매핑을
직접 확인했다. 작성 RTL의 금지 token 정적 검사와2-process 개수 검사도 통과했다.
근거는 해당 실행의 `run_manifest.json`, `synthesis_metrics.json`, `utilization.rpt`,
`vivado_project/f0.sim/sim_1/behav/xsim/simulate.log`와 `console.log`다.

경고는 숨기지 않는다. XPM의 사용하지 않는 내부 포트/레지스터 제거 및 충돌
메시지 안내, 작은 설계의 병렬 합성 미사용, xsim timescale/GCC 자동탐색 안내가
남았다. 작성 RTL의 폭 절삭·latch 경고는 확인되지 않았다. `timing_summary.rpt`는
내부 clock/endpoint/loop 누락0을 보고하지만38개 input·107개 output에 I/O delay가
없고 OOC clock source/배치배선도 미확정이다. 따라서100MHz의 전체 경로 timing
closure나 실물 처리 성능을 주장하지 않는다. 통합 top에서 I/O·clock 조건을
결정한 뒤 구현/배치배선과 보드 검증이 필요하다.
