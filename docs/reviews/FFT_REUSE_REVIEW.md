# 기존 FFT 재사용 검토 (MFCC 통합 관점)

> 이 문서가 인용하는 로그·스크립트는 저장소에 포함되지 않는 로컬 검토 작업 폴더에 있다. Vivado 버전별로 `<검토 폴더 2020.2>`, `<검토 폴더 2024.2>`로 표기한다.

최초 작성: 2026년 10월 4일. 정정 2판(Vivado 2024.2 재검증).
**정정 3판: 2026년 10월 4일** — 고정소수점 Q0/Q1 실험 결과를 반영해 C9~C11을 정정.
작성 범위: 기존 FFT 재사용 검토.
검토 대상: `D:\2610_MFCC\reference_code\previous_fft` (ZYBO Z7-20 R2^2SDF FFT).

이 문서는 **직접 실행한 결과만**을 근거로 한다. 기존 보고서
(`previous_fft/README.md`, `reports/phase*`)의 통과 기록은 근거로 사용하지 않았다.
재실행하지 않은 항목은 9장에 미검증으로 남겼다.

이번 작업에서 저장소 안에 작성·수정한 파일은 이 보고서와
`FFT_SPEC_HANDOFF.md` 두 개다. `MFCC_SPEC.md`는 수정하지 않았다.
`reference_code`, 구현 RTL, Python/C 코드는 읽기만 했다.
검증 자료는 `<검토 폴더 2020.2>`(Vivado 2020.2)와
`<검토 폴더 2024.2>`(Vivado 2024.2)에 분리 보관했다.
Git 커밋·푸시·브랜치 전환은 하지 않았다.

---

## 0. 결론 요약

### 판정: **연결·배율 보정 후 재사용 가능** (고정소수점 custom RTL 비교군)

`fft_stream_top`은 MFCC가 필요한 N=512 경로에서 **FFT 코어 내부 수정 없이** 사용할 수 있다.
독립 비트정확 모델과 29개 시뮬레이션 시나리오에서 불일치 0, 인덱스 오류 0, 프레임 오류 0이고,
**Vivado 2024.2와 2020.2에서 시뮬레이션 출력이 바이트 단위로 동일**하며,
2024.2에서 100 MHz OOC post-route 타이밍을 만족한다.

전제 세 가지:

1. **부동소수점 IP 비교군의 대체가 아니다.** 참고 MFCC(`github_mfcc`)의 FFT는
   float32 Xilinx `fft_block`(xfft)이다(`power_spectrum.v:79`). 기존 FFT는 16비트
   고정소수점이므로 네 비교군 중 **네 번째(고정소수점 custom RTL)** 자리에만 들어간다.
2. **출력 정지를 받아들일 수 없다.** 출력단에 흐름 제어가 없고, 코어에는 **정지 입력 포트 자체가
   존재하지 않는다**(4.5). 소비자가 멈출 수 있다면 FIFO + 프레임 수락 제한이 필요하며,
   이는 설계 추가 작업이다.
3. **FFT 단독 결과다.** power/Mel/log/DCT는 구현하지 않았고 실제 보드 검증도 없다.
   이 문서의 어떤 수치도 전체 MFCC 정확도나 보드 동작의 근거가 아니다.

### 1판에서 정정한 내용

| # | 1판 서술 | 정정 |
|---|---|---|
| C1 | "사용자 요구 버전은 2020.2" | **틀렸다.** 이 프로젝트가 사용하기로 정한 버전은 **Vivado 2024.2**다. 2020.2는 *기존 FFT 프로젝트*가 요구했던 버전(그쪽 OI-002)이며, 2020.2에서 얻은 결과는 해당 버전의 증거로 보존한다. 이번에 2024.2에서 전면 재검증했다(2.4). |
| C2 | checker 실행 결과를 통과로 서술 | `check_probe2.py`는 결과표를 출력한 뒤 `KeyError: 1`로 예외 종료했다(`logs/check_probe2.txt` 마지막 줄). **결과표 출력은 통과가 아니다.** 종료 코드를 갖는 `check_probe_fixed.py`로 교체하고 음성 대조(negative control)로 검출력을 확인했다(2.5). |
| C3 | "29개 시나리오" (크기 구분 없음) | **N=512 24개 + 그 외 5개**(N=256 2개, N=1024·N=128·N=64 각 1개). 따라서 N=512 외 크기의 근거는 크기당 1~2개뿐이다(2.3). |
| C4 | "프레임 내부에서는 실질적으로 valid-only 흐름이다", valid-only 공급 권장 | **과한 일반화였다.** 입력은 `in_valid && in_ready`가 모두 1인 cycle에만 진행한다. `in_ready`는 항상 1이 아니다(설정 전, `ST_DRAIN` 중 0). 시험에서 `in_ready`가 내려가지 않은 것은 그 자극의 성질이며 규칙이 아니다. 생산자는 handshake를 완전히 구현해야 한다(4.2). |
| C5 | "`core_ready`를 코어 stall 입력으로 **배선**" | **불가능하다.** `fft_r22sdf_core`의 포트 목록에 하류 ready/stall 입력이 **없고**, `natural_reorder_pingpong`에도 읽기측 정지 입력이 없다. 연결할 포트가 존재하지 않으므로 배선이 아니라 RTL 수정이다(4.5, 6.1). |
| C6 | AXI 문제를 "규약 위반"으로만 서술 | 판정 기준을 명확히 한다: **정지 중 데이터·`tvalid`·`tlast`를 수락 시점까지 유지하는가** → **유지하지 않는다**(hold/skid 레지스터 없음). 또 **외부 소비자 정지로 인한 손실**과 **내부 bank 충돌**은 서로 다른 경로다(4.5). |
| C7 | clamp·BFP 주장의 범위가 불명확 | 시험한 입력군·조건·로그 하한·shift 정책으로 한정했다. BFP 실험은 **합성 가우시안 잡음 + 자작 float64 Mel 필터뱅크**이며 실제 음성의 MFCC 정확도 검증이 아니다(5.2, 5.3). |
| C8 | `sweep_headroom_band.txt`를 근거로 제시 | 그 로그는 **저장되지 않은 인라인 명령**으로 생성되어 재현 자료가 없었다. 로그는 보존하되 **재현 자료 부족**으로 표시하고, 핵심 결론만 seed를 기록한 `sweep_clamp_check.py`로 축소 재현했다(5.3, 10.3). |
| **C9** (3판) | "미완료 프레임 최대 3개", "약 3N이면 연속 처리량에 충분" | **틀렸다.** 기록된 연속 실행에서 **최대 4개**가 겹친다(cycle offset 1,536에서 프레임 0~3). 3N 충분성 주장을 **철회**한다. 깊이와 credit 방식은 후속 protocol 설계에서 확정·검증한다(4.4, 6.1 M1-a). |
| **C10** (3판) | 5.2의 참고 실험을 "block floating point"라고 호칭 | **스크립트와 다르다.** 그 실험은 `α = 1`로 **먼저 양자화한 뒤 정수를 shift**한다. 양자화 전에 지수를 고르는 BFP와 다른 연산이다. 절 제목·설명을 실제 동작에 맞췄고, 두 방식의 측정 차이는 `FIXED_POINT_PILOT.md` 3장에 있다(5.2). |
| **C11** (3판) | 5.3의 "원인 특정 — 비대칭 최소 코드 `-32768`" | **과한 표현.** clamp가 없앤 것은 **시험한 두 입력군에서 관측된** overflow다. 유일 원인에 대한 일반 증명이 아니다. 제목과 서술을 고쳤다(5.3). |

### 항목별 판정

| 항목 | 판정 | 근거 |
|---|---|---|
| FFT 연산(순방향 부호, N=512) | 그대로 재사용 | 2.2, 2.3, 2.4, 3.1 |
| 출력 bin 순서(자연 순서) | 그대로 재사용 — 추가 재정렬 불필요 | 2.3, 3.2 |
| 비트폭·Q 형식 | 그대로 재사용 | 3.3 |
| 누적 스케일 | **배율 보정 필요** (출력 = DFT/N, S=log2 N) | 3.4, 6.1 |
| 반올림(ties-to-even) | 그대로 재사용 | 3.5 |
| 오버플로 처리 | **입력 제한 필요** (포화 아님, wrap) | 3.6, 5.3, 6.1 |
| 입력 handshake | 그대로 재사용 — `in_valid && in_ready` 준수 전제 | 4.2 |
| 프레임 경계 공백 | 동작 정상, **지연 비용 ~N cycle** | 4.3 |
| 출력 흐름 제어 | **없음. 코어에 정지 포트 없음** → FIFO + 수락 제한 설계 필요 | 4.5, 6.1 |
| 클록·리셋 | 그대로 재사용 (단일 클록, 동기 active-low) | 4.6 |
| 설정(`cfg_apply`) | 그대로 재사용 — 리셋 후 1회 펄스 필수 | 4.1 |
| 조용한 프레임 정밀도 | **입력 정규화 필요**; BFP는 후보(미채택) | 5.2 |
| 100 MHz OOC 타이밍·자원 | 2024.2에서 재현 | 2.6 |
| 실제 보드 동작 | **미검증** | 2.7, 9 |

### MFCC 처리 시간 여유 (FFT 단독)

참고 MFCC 규격은 frame 512 / hop 160(`get_frames.vhd:51-52`)이다. 16 kHz에서 hop 160은
**10.0 ms**다. 측정한 N=512 경로는 첫 입력 수락부터 마지막 출력까지 **1,559 cycle**,
100 MHz에서 **15.6 µs**다. 프레임 경계 drain(4.3)을 매번 겪어도 약 2,100 cycle = 21 µs다.
즉 FFT 단계만 보면 실시간 예산의 **0.16 ~ 0.21 %**를 쓴다.
(이는 FFT 단독 예산이며, power/Mel/log/DCT와 데이터 이동 비용은 포함하지 않는다.)

이 여유 때문에 실수 입력에 `in_im=0`을 넣어 복소 처리량 절반을 버려도 문제가 없다.
두 실수 프레임을 하나의 복소 FFT에 묶는 기법은 **권장하지 않는다**
(복소 입력은 오버플로 조건이 더 엄격하고(5.3), 후처리가 추가되며, 처리량 이득이 불필요하다).

---

## 1. 검증 환경

### 1.1 이 프로젝트가 사용하는 버전: Vivado 2024.2

| 항목 | 값 |
|---|---|
| OS | Windows 10 Education 10.0.19045 |
| **주 도구** | **Vivado 2024.2** (`C:\Xilinx\Vivado\2024.2`). 시뮬레이터 `Vivado Simulator v2024.2.0`, 구현 `Vivado v2024.2 (64-bit) SW Build 5239630` |
| 대상 소자 | `xc7z020clg400-1` |
| 참고 모델 | Python 3.11.9 + numpy 2.1.3 (RTL만 읽고 직접 작성한 비트정확 모델) |
| 작업 폴더 | `<검토 폴더 2024.2>` |

### 1.2 보존하는 Vivado 2020.2 증거

`D:\Xilinx\Vivado\2020.2`가 함께 설치되어 있다. **기존 FFT 프로젝트**는 2020.2를 요구했고
(그쪽 `docs/open_issues.md` OI-002, Phase 7 스크립트가 2020.2 외 버전을 거부),
기존 테스트벤치의 통과 기록도 2020.2에서 나온 것이다. 따라서 1판의 모든 2020.2 결과는
`<검토 폴더 2020.2>`에 **그 버전의 증거로 보존**하고 삭제·덮어쓰지 않았다.
2024.2 결과는 별도 폴더에 두어 두 버전을 비교할 수 있게 했다.

### 1.3 확인하지 않은 환경

| 항목 | 상태 |
|---|---|
| MATLAB | **설치 여부 미확인.** 기존 MATLAB 모델은 실행하지 않았다 |
| `iverilog`, `verilator`, `gcc` | PATH에 없음. 다른 시뮬레이터로 교차 검증하지 않았다 |
| ZYBO Z7-20 board file / PS7 preset | 미확보 |
| 보드 연결(JTAG/UART) | 확인하지 않음 |

### 1.4 소스 무결성

`<검토 폴더 2024.2>/src/rtl/**/*.sv` 20개 파일과 `mem/twiddle_1024_w16.mem`의 SHA-256이
`reference_code/previous_fft` 원본과 **전부 일치**함을 확인했다(불일치 0).
전체 해시는 `<검토 폴더 2024.2>/logs/source_hashes.txt`에 있다.
2024.2 자극(`scenarios.txt`, `expected_meta.json`, `s*_in.txt`)은 2020.2 실행과 **동일 해시**다.

---

## 2. 실행한 검증과 결과

증거 등급을 **시뮬레이션 / 합성·구현 / 실제 보드**로 구분한다.

### 2.1 [시뮬레이션, 2020.2] 기존 테스트벤치 재실행

기존 `tb/tb_fft_stream.sv`(Phase 6 전체 경로)와 `tb/tb_fft_axi_stream_wrapper.sv`(Phase 8)를
원본 입력·기대값과 함께 재실행했다. 이 두 TB는 2020.2에서만 재실행했다
(2024.2에서는 신규 TB 3종을 돌렸다 — 2.4).

| 테스트 | 시나리오 | 결과 | 로그 |
|---|---|---|---|
| `tb_fft_stream` | 6개(N=16 1프레임, N=1024 4종, N=1024 3프레임 연속) | **PASS**, 출력 7,184/7,184, 불일치 0, overflow 0, 공백 0 | `<검토 폴더 2020.2>/logs/phase6_rerun_xsim.log` |
| `tb_fft_axi_stream_wrapper` | 2개(N=16 no-stall, 에러 상태 관찰) | **PASS**, 출력 16/16, protocol sticky=1, backpressure sticky=1 | `<검토 폴더 2020.2>/logs/phase8_rerun_xsim.log` |

첫 출력 지연은 N=16에서 42, N=1024에서 2,076 cycle로 기존 보고서와 같았다.

### 2.2 [시뮬레이션] 독립 비트정확 모델

기존 MATLAB 모델을 쓰지 않고 RTL만 읽어서 Python 비트정확 모델을 작성했다
(`fft_model.py`). 3장이 그 모델의 근거다.

| 검증 | 결과 |
|---|---|
| twiddle ROM 독립 재계산 (`cos/sin(-2πa/1024)`, Q1.15, ties-to-even, 계수 포화) | 1,024 entry 전부 일치 |
| 모델 vs 기존 Phase 6 기대값 파일 5종 | 불일치 0, overflow 플래그 일치 |
| 모델 vs `numpy.fft.fft(x)/N` (N=8…1024 실수 가우시안) | 최대 절대오차 약 1 LSB(Q1.15) |

→ 모델이 RTL과 수학적 FFT 양쪽에 맞으므로 이후 기준값으로 사용했다.

### 2.3 [시뮬레이션] 신규 시나리오 29개 — 크기별 구분

기존 `tb_fft_stream.sv`는 **입력 공백을 금지한다**(`:183`에서 `$fatal`, `:276`에서 실패 판정).
또 기존 시뮬레이션은 **N=512와 N=128을 한 번도 돌리지 않았다**
(Phase 5: 8/16/32/64/256/1024, Phase 6: 16/1024). MFCC가 쓰는 크기가 N=512이므로
새 TB를 작성했다(`src/tb/tb_mfcc_fft_probe.sv`, `src/tb/tb_mfcc_fft_directed.sv`).

**시나리오 29개의 크기 분포 (C3 정정):**

| N | 시나리오 수 | id |
|---:|---:|---|
| **512** | **24** | 1–10, 13, 14, 16, 20–29, 31 |
| 256 | 2 | 11, 30 |
| 1024 | 1 | 15 |
| 128 | 1 | 12 |
| 64 | 1 | 17 |
| 합계 | **29** | — |

즉 **N=512는 24개 시나리오로 두껍게 덮였지만, 다른 크기는 크기당 1~2개뿐이다.**
MFCC가 N=512만 쓴다면 충분하고, 런타임 크기 변경을 쓰려면 해당 크기의 시험을 늘려야 한다.
크기 변경 자체는 id 30/31(512→256→512)에서 확인했다.

내용별 분류: 결정론적 수치 4(impulse, DC 2종, 정수 bin 정현파), 난수 입력 7,
연속 프레임 4(3·4·8프레임 및 2프레임), 프레임 내 공백 5, 프레임 경계 공백 3,
오버플로 경계 3, BFP 상향 1, 최소 음수 1, 크기 변경 2.

**결과(2020.2와 2024.2 모두 동일):** 29개 전부 비트정확 일치, `out_index` 오류 0,
`out_last` 오류 0, `input_frame_error`/`reorder_frame_error`/`bank_collision_error`/
`config_consistency_error` 모두 0, overflow 플래그는 모델과 일치(id 24·29에서만 1).
비교한 `out_valid` beat 총 **22,720**개.

결정론적 확인값(배율·부호·순서의 직접 증거):

- impulse `x[0]=32767` → **512개 bin 전부 64** = `round(32767/512)` → 출력 = DFT/N
- DC `x[n]=16384` → `bin0 = 16384`, 나머지 **정확히 0**
- `0.9·cos(2π·40·n/N)` → `bin40 = bin472 = 14744`(이상값 14746), 허수부 0
  → 자연 순서, 실수 입력의 공액 대칭, 순방향 부호 확인

지향 검사 **28/28 PASS**: 리셋 직후 상태, 설정 전 `in_ready`=0, 범위 외 설정 거부,
busy 중 설정 거부, 프레임 중간 리셋 후 복구, 잘못된 `in_last` 검출.
리셋 후 재설정하고 넣은 N=512 프레임도 비트정확 일치였다.

### 2.4 [시뮬레이션, 2024.2] 재검증

동일 RTL 복사본·동일 자극·동일 TB로 2024.2에서 재실행했다.

| 단계 | xvlog | xelab | xsim |
|---|---:|---:|---:|
| probe1 (17 시나리오) | 0 | 0 | 0 |
| probe2 (12 시나리오) | 0 | 0 | 0 |
| directed (28 검사) | 0 | 0 | 0 |

runner 종료 코드 0. checker 종료 코드 0(probe·probe2 각각).

**버전 간 비교:** `probe_out.csv`, `probe_summary.csv`, `directed_out.csv`가
2020.2 결과와 **바이트 단위로 동일**하다(SHA-256 일치, `diff` 차이 없음).
`PROBE` 라인의 지연·공백 수치도 모두 같다(N=512 지연 1048, 경계 공백 537, N=1024 1054 등).
즉 **이 RTL은 두 시뮬레이터 버전에서 동일하게 동작한다.**

- 실행 명령·종료 코드·수치: `<검토 폴더 2024.2>/logs/RESULTS_2024_2.txt`
- 로그: `logs/probe1_*.log`, `logs/probe2_*.log`, `logs/directed_*.log`, `logs/run_sim_2024_2_console.log`
- 비교 결과: `logs/check_probe_2024_2.txt`, `logs/check_probe2_2024_2.txt`

주의: 2024.2 재실행 중 내 runner(`run_sim_2024_2.ps1`) 초판이 모든 단계가 0인데도
`SIM OVERALL: FAIL`을 출력했다. PowerShell 함수가 narration까지 출력 스트림에 담아
종료 코드 배열이 오염된 탓이다. runner를 `Write-Host` + 스크립트 범위 리스트로 고쳐
재실행했고, 위 표는 수정판의 결과다. (판정 로직 자체의 버그였으므로 기록한다.)

### 2.5 [도구 정정] checker 수정과 검출력 확인

**문제:** `check_probe2.py`는 A·B·C 결과표를 전부 출력한 뒤 D절에서 `KeyError: 1`로
예외 종료했다(round2 매니페스트에 시나리오 1이 없는데 무조건 조회). 1판은 표가 나온 것을
근거로 삼았는데, **표 출력과 전체 검증의 정상 종료는 다르다.**

**수정:** `check_probe_fixed.py`로 교체했다.

- 매니페스트에 있고 출력이 없는 시나리오, 출력이 있고 매니페스트에 없는 시나리오,
  beat 수 불일치, `seq` 불연속, 비트 불일치, `out_index`가 0…N-1의 순열이 아닌 경우,
  `out_last` 오류, overflow 플래그 불일치, RTL 오류 플래그 ≠ 0, `observed ≠ driven`,
  처리 중 예외 → **전부 실패**로 집계
- D절(결정론적 점검)은 해당 시나리오가 없으면 `SKIP`으로 표시하고 계속 진행
- 마지막에 `VERIFICATION STATUS` 블록(매니페스트 수 / 출력이 있는 수 /
  **완전히 비교된 수** / beat 수 / 기대 beat 수 / 실패 수)을 찍고
  `RESULT: PASS` 또는 `RESULT: FAIL  (a printed table is not a pass)`을 출력하며
  **종료 코드 0/1**을 반환

**검출력 확인(음성 대조):** checker가 실제로 실패하는지 확인했다.

| 주입한 결함 | 결과 | 종료 코드 |
|---|---|---|
| 샘플 1개의 `out_re`를 +1 | `FAIL id=20 bit_mismatch: 1 sample(s) differ` | 1 |
| id=21 출력 512 beat 전부 삭제 + id=27을 96 beat 잘라냄 | `FAIL id=21 no_output`, `FAIL id=21 beat_count`, `FAIL id=27 beat_count` | 1 |

로그: `<검토 폴더 2020.2>/logs/check_probe_fixed_negative_controls.txt`.
수정 전 스크립트(`check_probe.py`, `check_probe2.py`)와 그 로그는 증거 추적을 위해
보존했고, 이후 모든 판정은 `check_probe_fixed.py`를 기준으로 한다.

### 2.6 [합성·배치배선] OOC 구현

`run_ooc_impl.tcl`로 `fft_stream_top`을 비프로젝트 OOC 흐름
(`synth_design -mode out_of_context` → `opt`/`place`/`phys_opt`/`route`)으로 돌렸다.
제약은 100 MHz 단일 생성 클록 + `set_clock_uncertainty 0.200`이다.

| 항목 | **Vivado 2024.2** | Vivado 2020.2 | 기존 보고서(2020.2) |
|---|---|---|---|
| post-route WNS | **+0.106 ns** | +0.197 ns | +0.255993 ns |
| TNS / 실패 엔드포인트 | 0.000 / **0 (22,432)** | 0.000 / 0 (22,396) | 0.000 / 0 (22,396) |
| WHS | +0.017 ns, 실패 0 | +0.016 ns, 실패 0 | (미기재) |
| Slice LUT | **5,138** (logic 2,879 / DRAM 2,142 / SRL 117) | 5,109 (2,850 / 2,142 / 117) | 5,134 (2,876 / 2,142 / 116) |
| Slice Register | **1,304** | 1,292 | 1,292 |
| RAMB36 / RAMB18 | **3 / 0** | 3 / 0 | 3 / 0 |
| DSP48E1 | **20** | 20 | 20 |
| DRC Error / Critical | **0 / 0** | 0 / 0 | 0 / 0 |
| DRC Warning | DPOP-2 ×10, RTSTAT-10 ×1 | DPOP-2 ×10, RTSTAT-10 ×1, ZPS7-1 ×1 | 동일 분류 |
| 콘솔 severity | Error 0, Critical 0, Warning 66 | Error 0, Critical 0, Warning 44 | — |
| Vivado 종료 코드 | **0** | 0 | — |

→ **100 MHz OOC post-route 통과를 2024.2에서 재현했다.** 자원은 세 결과가 사실상 같다
(LUT 차이 0.6 % 이내). WNS가 2020.2보다 작은 것(0.106 vs 0.197)은 버전 간 타이밍 모델·
배치배선 차이이며, 둘 다 만족이고 실패 엔드포인트는 0이다. 여유가 0.106 ns로 줄었으므로
**실제 Zynq 블록 디자인(PS7·AXI 포함)에서는 100 MHz 여유를 다시 확인해야 한다.**

ZYBO Z7-20(`xc7z020`) 기준 점유율은 LUT 9.66 %, FF 1.23 %, BRAM 2.14 %, DSP 9.09 %로
MFCC의 power/Mel/log/DCT 단계를 추가할 여유가 있다(그 단계들의 실제 비용은 미측정).

2024.2 경고 66건은 전부 OOC 흐름 산물이거나 의도된 동작이다:
`Route 35-197/35-198`(×41, `HD.CLK_SRC`/`HD.PARTPIN_LOCS` 미지정),
`DRC 23-814`(×4, OOC라서 연결 기반 DRC 일부 생략), `Timing 38-242`(×3, 같은 사유),
`Constraints 18-8777`(×1), `Synth 8-7080`(×1, 빌드 병렬성),
`Synth 8-3936`(×12, 미사용 상위 비트 제거 — `local_ordinal` 11→10비트,
`root_exponent` 22→10비트. `rom_address = root_exponent[9:0]`이므로 의도된 동작),
`Synth 8-4767`(×4, delay 1~2짜리 축퇴 stage의 배열을 레지스터로 구현 — 예상된 동작).
설계 결함을 가리키는 경고는 없다. DRC를 "warning-free"로 표현하면 안 된다.

`$readmemh` 성공을 2024.2 합성 로그에서 확인했다
(`INFO: [Synth 8-3876] $readmem data file 'twiddle_1024_w16.mem' is read successfully`).
6.1 M7 참고.

- 리포트: `<검토 폴더 2024.2>/work/synth_fft_stream_top_10.000/post_route_{timing,utilization,drc}.rpt`
- 로그: `<검토 폴더 2024.2>/logs/ooc_fft_stream_top_100mhz.log`, `logs/ooc_console.log`
- 체크포인트: `.../post_route.dcp`

### 2.7 [실제 보드] 미실행

비트스트림 생성, XSA/BSP/ELF, JTAG 프로그래밍, UART, DMA 전송, 보드 측정은
**이번에도 전혀 실행하지 않았다.** 이유:

- 기존 저장소에도 `fft_zynq.xpr`, `system.xsa`, `system.bit`, `BOOT.bin`, `.elf`가 없다
  (`reports/phase8_integration_status.md`의 "Artifacts that do not exist").
- 신뢰할 수 있는 ZYBO Z7-20 board preset / PS7 DDR·MIO 설정이 미확보다(기존 OI-013).
- 보드 연결 여부를 확인하지 않았다.

**FFT의 보드 동작은 여전히 증거가 없다.** 2024.2 재검증이 이를 바꾸지 않는다.

---

## 3. 소스에서 확인한 FFT 규격

모든 항목은 RTL 라인을 근거로 하고 2.2~2.4의 실행 결과로 교차 확인했다.
경로는 `reference_code/previous_fft/` 기준이다.

### 3.1 연산

| 항목 | 값 | 근거 |
|---|---|---|
| 변환 | 순방향 복소 FFT, `W = exp(-j2πk/N)` | `matlab/export_twiddle_rom.m:22`, ROM 독립 재계산 일치 |
| 구조 | DIF, single-path R2^2SDF, 물리 stage 5쌍 | `rtl/top/fft_r22sdf_core.sv` generate 5개 group |
| 지원 크기 | `cfg_log2_n = 3…10` → N = 8…1024 | `fft_stream_top.sv:66-67`, `fft_r22sdf_core.sv:87-88` |
| 시뮬레이션으로 확인한 크기 | **512(24개), 256(2개), 1024·128·64(각 1개)** | 2.3 |
| 입력 | 자연 순서, 복소(`in_re`, `in_im`) | 포트 |
| 실수 입력 | `in_im = 0`으로 지원. 전용 real-FFT 경로 **없음** | 2.3 |

### 3.2 입출력 순서

| 경계 | 순서 | 근거 |
|---|---|---|
| 입력 | 자연 순서 n = 0…N-1, `in_last`를 n=N-1에 | `fft_r22sdf_core.sv:190,198` |
| 코어 출력 | bit-reversed bin 순서, `out_ordinal` = 출력 순번 | `fft_r22sdf_core.sv:152-153` |
| 최종 출력 | **자연 순서 bin 0…N-1** (`out_index`가 bin 번호) | `natural_reorder_pingpong.sv` + 인덱스 오류 0 |

→ **MFCC 쪽에 추가 재정렬 버퍼가 필요하지 않다.** `out_index`를 bin 번호로 쓰고
0…256(N/2+1)만 통과시키면 된다.

### 3.3 비트폭과 Q 형식

| 경계 | 폭 / 형식 | 근거 |
|---|---|---|
| 입력 `in_re`/`in_im` | signed 16, **Q1.15** | `r22sdf_runtime_group.sv:59` |
| Type-I stage 출력 | signed 17, Q2.15 (성장 1비트, 오버플로 불가) | `:59`, `bf_type1.sv`(CALC_W = OUT_W) |
| Type-II stage 출력 | signed 18, Q3.15 (성장 1비트) | `:70`, `bf_type2.sv` |
| twiddle 계수 | signed 16, **Q1.15**, 계수만 포화(+1.0 → 32767) | `twiddle_rom_sync.sv`, `export_twiddle_rom.m` |
| twiddle 곱 누산 | signed 35 | `r22sdf_runtime_group_quantize.sv:36-37` |
| twiddle 곱 재양자화 | 35 → `>>15` → signed 18, Q3.15 | `:97,103` |
| group 경계 shift | 18 → `>>2` → signed 16, **Q1.15** | `:166,171` |
| 단독 Type-I(홀수 log2 N) | 17 → `>>1` → signed 16, Q1.15 | `r22sdf_runtime_group.sv:91,96` |
| 출력 `out_re`/`out_im` | signed 16, **Q1.15** | `fft_stream_top.sv` 포트 |
| `out_index` | 10비트 | 포트 |

ROM은 `twiddle_1024_w16.mem` 1,024 entry × 32비트, `{real[15:0], imag[15:0]}`이다.
`twiddle_1024_w18.mem`(18비트)도 있으나 **런타임 RTL은 w16만 쓴다**.

### 3.4 누적 스케일 — 배율 보정 필요

완전 group 1개 = 2비트 성장 + `>>2`, 단독 Type-I = 1비트 성장 + `>>1`.
총 축소 **S = log2 N**이고

```
out(Q1.15) = DFT(in) / 2^S = DFT(in) / N
```

직접 확인: impulse 32767 → 전 bin 64 = `round(32767/512)`; DC 16384 → `bin0 = 16384`.
**N=512에서 S = 4×2 + 1 = 9.**

입력을 `in = round(α·u·32768)`로 넣었을 때 `P[k] = |DFT(u)[k]|²/N` 정의와의 관계는

```
P[k] = N · (out_re² + out_im²) / (α² · 2^30)
```

`α = 1/2`, `N = 512`이면 **`P[k] = (out_re² + out_im²) / 2^19`** (= / 524288).
자세한 적용 조건은 `FFT_SPEC_HANDOFF.md` 3장에 있다.

### 3.5 반올림

전 구간 **convergent rounding (ties-to-even)**: `fixed_round_shift.sv`의
`ROUND_CONVERGENT = 1`이 모든 인스턴스에 적용된다
(`r22sdf_runtime_group_quantize.sv:99,105,168,173`, `r22sdf_runtime_group.sv:93,98`).
독립 모델에서 같은 규칙으로 구현해 비트정확 일치했다.

### 3.6 오버플로 처리 — 포화가 아니라 wrap

`fixed_round_shift.sv:69` `assign out_data = rounded_value[OUT_W-1:0];` — 상위 비트를 **버린다**.
`:72`의 `overflow`는 sign extension 파괴를 **검출만** 한다. `bf_type1/2.sv`도 같다.

즉 **오버플로가 나면 포화되지 않고 반대 부호로 감싸인다.** 측정한 피해:

| 시나리오 | NMSE(이상 FFT 대비) | 최대 오차 |
|---|---|---|
| id 24 실수 full-scale 오버플로 프레임 | **-15.1 dB** | **513 LSB** |
| id 25 같은 프레임 1비트 여유 | -61.8 dB | 1.2 LSB |
| id 29 복소 full-scale 오버플로 프레임 | **-2.5 dB** | **4,841 LSB** |

플래그 전파는 정상이다. 샘플 overflow가 프레임 플래그로 누적되고
(`fft_r22sdf_core.sv:155`, `natural_reorder_pingpong.sv`의 `bank_frame_overflow`),
해당 프레임의 **모든 출력 beat에서 `out_overflow`가 1**로 재생된다.
프레임 간에 누적되지는 않으므로 프레임별로 받아 기록하면 된다.

---

## 4. 인터페이스·흐름 제어·클록/리셋

### 4.1 설정 — 리셋 후 `cfg_apply` 펄스가 필수

`fft_stream_top.sv:75` `assign in_ready = configured && core_in_ready;`
`configured`는 설정이 1회 수락되어야 1이 된다. **리셋 후 `cfg_apply` 펄스 전에는
어떤 입력도 수락되지 않는다.**

| 규칙 | 확인 |
|---|---|
| 리셋 후 `cfg_ready`=1, `busy`=0, `active_log2_n`=10, `in_ready`=0 | PASS |
| 입력을 64 cycle 제시해도 설정 전에는 수락 0, 출력 0 | PASS |
| `cfg_log2_n` = 2 또는 11 → `cfg_reject`=1, `cfg_error`=1, `in_ready` 0 유지 | PASS |
| `cfg_log2_n` = 9 → `cfg_accept`=1, `active_log2_n`=9, `in_ready`=1 | PASS |
| busy 중 `cfg_apply` → `cfg_reject`=1, `active_log2_n` 유지, 프레임 정상 완료 | PASS |
| 크기 변경 512 → 256 → 512 | PASS, 양쪽 비트정확 |

`cfg_apply`는 **idle에서 1 cycle 펄스**여야 한다. AXI 래퍼는 GPIO bit4의 rising edge로
만든다(`fft_axi_stream_wrapper.sv:78`) — GPIO bit를 1로 유지하면 펄스는 1회뿐이다.
`cfg_error`는 다음 성공 설정까지 **sticky**다(`fft_stream_top.sv:152-157`).

### 4.2 입력 handshake — `in_valid && in_ready` (C4 정정)

**규칙:** 샘플 수락과 모든 카운터(`input_count`, stage의 `sample_counter`/`fill_count`,
token 전파)는 **`in_valid`와 `in_ready`가 동시에 1인 cycle에만** 진행한다
(`fft_r22sdf_core.sv:117` `assign accepted_input = in_valid && in_ready;`,
`:91-115`의 `scheduled_token`).

**`in_ready`는 항상 1이 아니다**(`fft_r22sdf_core.sv:84-85`, `fft_stream_top.sv:75`):

- 설정 전(`configured == 0`): 0 — 영구히
- `ST_DRAIN` 중: 0 — 약 N cycle (4.3)
- `ST_IDLE`에서 `cfg_apply`가 1인 cycle: 0

따라서 **생산자는 handshake를 완전히 구현해야 한다.** 샘플을 제시한 뒤
수락될 때까지 데이터와 `in_last`를 유지해야 한다.
1판의 "프레임 내부는 실질적으로 valid-only"라는 서술과 valid-only 공급 권장은 철회한다.

**시험이 보여준 것(한정된 범위):** `in_valid`를 프레임 **내부에서** 내리면 파이프라인이
진행하지 않고 결과가 바뀌지 않는다.

| 시나리오 | 입력 구간 | 출력 구간 | 출력 공백 | `in_ready`가 0이 된 횟수 | 비트정확 |
|---|---:|---:|---:|---:|---|
| id 20 매 샘플 1 cycle 공백 | 1,023 | 512 | 0 | 0 | 일치 |
| id 21 매 샘플 7 cycle 공백 | 4,089 | 512 | 0 | 0 | 일치 |
| id 22 후반 300 cycle 정지 | 812 | 512 | 0 | 0 | 일치 |
| id 8 무작위 1~4 cycle 공백 | 545 | 512 | 0 | 0 | 일치 |
| id 23 2프레임, 내부 공백 | 1,112 | 1,024 | 0 | 0 | 일치 |

이 다섯 경우에 `in_ready`가 내려가지 않은 것은 **그 자극이 `ST_DRAIN`을 유발하지 않았기
때문**이며, `in_ready`를 무시해도 된다는 뜻이 아니다. 결론은
"프레임 내부 공백은 결과를 바꾸지 않는다"까지이고, 흐름 제어 규칙은 handshake 그대로다.

### 4.3 프레임 경계 공백 — 동작 정상, 지연 비용 있음

프레임의 마지막 샘플 직후 **1 cycle**이라도 `in_valid`를 내리면 코어는 `ST_BETWEEN`에서
dummy token을 넣고 `ST_DRAIN`으로 들어가 N-1 cycle 파이프라인을 비운다
(`fft_r22sdf_core.sv:207-224`). `ST_DRAIN` 중 `in_ready`는 0이다.

| 시나리오 | 요청한 경계 공백 | 실제 입력 정지 | 출력 공백 | 출력 개수 | 비트정확 |
|---|---:|---:|---:|---:|---|
| id 9 (N=512) | 1 cycle | **537 cycle** | 537 | 1,024/1,024 | 일치 |
| id 10 (N=512) | 40 cycle | 537 cycle | 537 | 1,024/1,024 | 일치 |
| id 15 (N=1024) | 1 cycle | **1,054 cycle** | 1,054 | 2,048/2,048 | 일치 |

비용은 약 `N + 25` cycle이고 **데이터는 손실되거나 틀어지지 않는다**(에러 플래그 0).
MFCC 예산(10 ms = 1,000,000 cycle) 대비 537 cycle = 5.4 µs는 무시할 수 있다.

### 4.4 연속 프레임 처리량 — 1 complex sample/clock 유지

| 시나리오 | 프레임 | 출력 | 출력 구간 | 공백 | `bank_collision_error` |
|---|---:|---:|---:|---:|---:|
| id 7 | 3 | 1,536 | 1,536 | 0 | 0 |
| id 13 | 4 | 2,048 | 2,048 | 0 | 0 |
| id 27 | **8** | 4,096 | 4,096 | 0 | 0 |

첫 출력 지연(첫 입력 수락 → 첫 출력, 내 측정 기준):

| N | 64 | 128 | 256 | 512 | 1024 |
|---|---:|---:|---:|---:|---:|
| cycle | 145 | 274 | 535 | **1,048** | 2,077 |

기존 보고서의 N=1024 값(2,076)과 1 cycle 차이는 측정 기준 차이다(일관된 오프셋).

**[3판 정정 C9]** 1·2판은 "수락됐지만 출력이 끝나지 않은 프레임이 최대 3개"라고 적었다.
**틀렸다.** 기록된 연속 실행(N=512, 측정 첫 출력 지연 1,048 cycle, 입력·출력 모두 무공백)에서
프레임 f의 입력은 cycle `512f`에, 출력 마지막 beat는 `1048+512f+511 = 1559+512f`에 있다.
cycle offset 1,536에서 프레임 0·1·2·3이 동시에 "수락됐으나 출력 미완료" 상태이므로
**최대 4개**다(`verification/fixed/analyze_pilot.py` E절에서 열거로 확인).

따라서 이 숫자에서 끌어낸 **"약 3N이면 연속 처리량에 충분하다"는 주장을 철회한다.**
FIFO 깊이와 credit 방식은 이 겹침 수만으로 정해지지 않는다 — 출력 버스트 길이, 소비자의
최악 정지 시간, 프레임 수락 규칙이 함께 걸린다. **후속 protocol 설계에서 확정·검증한다.**

### 4.5 출력 흐름 제어 — 정지 포트가 존재하지 않는다 (C5·C6 정정)

**(a) 코어에 정지 입력이 없다.** `fft_r22sdf_core`의 포트 목록은
`clk, rst_n, cfg_log2_n, cfg_apply, cfg_ready, cfg_error, active_log2_n,
in_valid, in_ready, in_re, in_im, in_last, out_valid, out_re, out_im,
out_ordinal, out_index, out_last, overflow, busy, input_frame_error,
core_token_out, core_token_tag`뿐이다. **하류 ready / stall / enable 입력이 없다.**
`natural_reorder_pingpong`에도 읽기측을 멈추는 입력이 없다 — `read_active`가 서면
N beat를 무조건 내보낸다(`:178-205`).

따라서 1판의 "`reorder_core_ready`를 코어 stall 입력으로 배선" 제안은 **성립하지 않는다.**
`reorder_core_ready`는 `fft_stream_top.sv:64`에서 선언되고 `:122`에서 포트에 연결되지만
읽는 곳이 없고, **받아줄 포트가 애초에 없다.**

전역 stall을 넣는 것은 RTL 수정이다. 최소한 다음을 모두 enable로 묶어야 한다:
`fft_r22sdf_core`의 `scheduled_token`과 상태기 카운터(`state`, `input_count`,
`dummy_count`, `output_count`, `pending_frames`), 각 `r22sdf_runtime_stage`의
`in_token_valid`와 `sample_counter`/`fill_count`/출력 레지스터,
`r22sdf_runtime_group_quantize`의 pre/s0/s1/out 파이프라인 레지스터,
재정렬 버퍼의 쓰기·읽기 카운터와 bank 역할. 파이프라인이 이미 token gating 구조이므로
구조적으로는 가능하지만 **이번 작업에서 시도하지 않았고 검증하지 않았다.**

**(b) 손실 경로가 두 개이고 서로 다르다.**

| 경로 | 조건 | 증상 | 플래그 | 이번 관측 |
|---|---|---|---|---|
| ① 내부 bank 충돌 | 코어가 쓰려는 bank가 full (`core_ready`=0) | 재정렬 버퍼가 그 쓰기를 **버린다**(`core_accept`가 0이라 메모리에 안 써짐) | `bank_collision_error` | 29개 시나리오 전부 0. 같은 크기 연속 프레임 8개까지 미발생 |
| ② 외부 소비자 정지 | `m_axis_tready`=0 (또는 소비자가 `out_valid`를 못 받음) | 그 beat가 **사라진다** | `backpressure_error_sticky`(bit 5)만 | 2020.2 AXI TB에서 sticky 비트 상승만 확인 |

**두 경로는 독립이다.** 재정렬 버퍼의 읽기측은 멈출 수 없으므로 **외부 정지는 bank 충돌을
일으키지 않는다** — ②가 발생해도 `bank_collision_error`는 0이다.
반대로 출력 FIFO를 붙여 ②를 막아도 ①에는 아무 영향이 없다(①은 코어→재정렬 구간 문제).

**(c) AXI 출력 판정 기준: 정지 중 데이터·`tvalid`·`tlast`를 수락 시점까지 유지하는가.**

→ **유지하지 않는다.** `fft_axi_stream_wrapper.sv:83-86`은

```systemverilog
assign m_axis_tdata  = {out_im, out_re};
assign m_axis_tvalid = out_valid;
assign m_axis_tlast  = out_last;
```

로 `fft_stream_top`의 출력을 **조합적으로 그대로 내보낸다.** hold/skid 레지스터가 없다.
결과:

1. `m_axis_tready`=0인 cycle에 FFT는 계속 진행하므로 다음 cycle에 `out_*`가 바뀐다.
   정지됐던 beat의 **데이터는 유지되지 않고 소실된다.**
2. `m_axis_tvalid`는 `out_valid`를 따라가므로, handshake 없이 1→0으로 떨어진다
   (프레임 사이, 또는 정지 중). AXI4-Stream은 TVALID를 세운 뒤 handshake 전에
   내리지 못하게 하므로 **이 자체가 규약 위반이다.**
3. `m_axis_tlast`도 유지되지 않으므로, 마지막 beat가 정지에 걸리면 **프레임 경계 정보가
   사라진다.** 수신측은 프레임 길이를 다른 수단으로 복원해야 한다.
4. `:158-159`는 `out_valid && !m_axis_tready`를 sticky 에러로 **기록만** 한다.
   손실을 막지 못한다.

2020.2 AXI TB의 "backpressure 테스트"는 이 sticky 비트가 올라가는 것만 확인하며,
출력 데이터·개수·gap·TLAST 필드를 의도적으로 `NA`로 기록한다
(`reports/phase8_integration_status.md`). 즉 **정지 시 무엇이 손실되는지는 기존 검증에도
없고 이번에도 정량화하지 않았다.**

### 4.6 클록과 리셋

| 항목 | 값 | 근거 |
|---|---|---|
| 클록 도메인 | `clk` 단일. CDC 없음 | 전체 RTL |
| 리셋 | `rst_n`, active-low, **동기** | 전 모듈 `always_ff @(posedge clk) if (!rst_n)` |
| 리셋 플립플롭 | 1,279 동기 reset + 13 동기 set (2020.2 기준 1,292) | `<검토 폴더 2020.2>/logs/post_route_utilization.rpt` |
| 리셋되지 않는 상태 | SDF delay line, 재정렬 bank, `twiddle_rom_sync`의 출력 레지스터 | `r22sdf_runtime_stage.sv`, `natural_reorder_pingpong.sv`, `twiddle_rom_sync.sv:24-26` |
| 리셋 후 첫 프레임 | 정상. 단 `cfg_apply` 필요(4.1) | 지향 검사 D1·D4 |
| 프레임 중간 리셋 | 복구 확인. 리셋 후 `cfg_ready`=1, `busy`=0, 에러 해제, 재설정 후 비트정확 | 지향 검사 D6 |

데이터 메모리가 리셋되지 않아도 `filled`/token/tag 게이팅 때문에 출력 유효성에 영향이 없음을
확인했다. **리셋이 동기이므로 리셋 구간에 클록이 공급되어야 한다.**

### 4.7 `in_last` 규칙

`in_last`는 N을 정의하지 않고 **검사만 한다**(`fft_r22sdf_core.sv:190,198,210`).
n=N-1에서 1이 아니면 `input_frame_error`가 서지만 **프레임은 그대로 진행한다**.
지향 검사에서 조기 `in_last`와 누락 모두 플래그가 올라감을 확인했다.

---

## 5. 수치 특성과 MFCC 영향 (조건 명시)

2.2에서 비트정확이 확인된 모델로 스윕했다. 출력은
`<검토 폴더 2020.2>/logs/sweep_range.txt`, `sweep_range2.txt`, `sweep_headroom.txt`,
`sweep_clamp_check.txt`에 있다. 재현 자료 상태는 10.3 참고.

### 5.1 출력 양자화 잡음은 신호 크기에 거의 의존하지 않는다

N=64…1024, 입력 레벨 전 구간에서 **FFT 출력 오차**의 RMS는 **약 0.6 LSB(Q1.15)**,
최대 약 1.3 LSB로 거의 일정했다. 출력 SNR은 입력이 작아질수록 그대로 나빠진다.

**[3판 주의]** 이것은 시험한 합성 신호에서 측정한 **FFT 출력 오차**의 크기다.
여기서 유도한 오차 전력을 **모든 신호의 실제 Mel 에너지 하한으로 부르면 안 된다.**
실제 Mel 에너지는 신호에 따라 그보다 작을 수도 클 수도 있다. 개발 음성에서의 실제
거동은 [FIXED_POINT_PILOT.md](FIXED_POINT_PILOT.md) 4장에서 측정했다.

### 5.2 조용한 프레임의 다이내믹 레인지 손실

**시험 조건(이것 밖으로 일반화하지 말 것):** N=512. 입력은 `numpy` seed 99로 만든
가우시안 잡음 1개를 peak 1로 정규화한 뒤 Hamming 윈도를 곱하고 목표 peak로 스케일한
합성 신호다. **실제 음성이 아니고, pre-emphasis를 적용하지 않았다.**
기준값은 **같은 양자화 입력**의 float64 `numpy.fft`이므로 입력 양자화 오차는 제외된다.

| 입력 peak | dBFS | 출력 최대 \|X\| (LSB) | NMSE (dB) | 최소 bin \|X\| (LSB) |
|---:|---:|---:|---:|---:|
| 1.0 | 0 | 686 | -54.1 | 26 |
| 0.25 | -12 | 171 | -42.4 | 6 |
| 0.0625 | -24 | 43 | -30.0 | 2 |
| 0.0312 | -30 | 22 | -24.3 | **0** |
| 0.01 | -40 | 7 | -14.5 | **0** |
| 0.003 | -50 | 3 | -4.0 | **0** |

Mel 에너지 로그 오차(아래 표)의 **추가 조건**: Mel 필터뱅크는 내가 작성한 float64
26-band 삼각 필터(16 kHz, `fmin=0`, `fmax=8000`, bin edge는 `floor`, 정규화 없음)이며
**`MFCC_SPEC.md`의 필터뱅크 정의와 비트 단위로 맞춘 것이 아니다.**
로그 하한은 비율 계산 안정화를 위한 `1e-30`을 분자·분모에 더한 것이고,
**공통 규격의 정규화 로그 하한 `1e-12`를 적용한 것이 아니다.** DCT·lifter·delta는 없다.

| 입력 peak | 최대 \|Δlog\| (dB) | 평균 \|Δlog\| (dB) | 1 dB 초과 band |
|---:|---:|---:|---:|
| 1.0 | 0.011 | 0.003 | 0 / 26 |
| 0.25 | 0.066 | 0.015 | 0 / 26 |
| 0.0625 | 0.252 | 0.064 | 0 / 26 |
| 0.01 | **1.19** | 0.293 | 1 / 26 |
| 0.003 | **2.57** | 0.933 | **13 / 26** |

→ **이 합성 신호에서는** 입력 peak가 대략 -24 dBFS 이상이면 Mel 로그 오차가 0.25 dB 이하,
-40 dBFS 부근에서 1 dB를 넘고, -50 dBFS 이하에서는 쓸 수 없다.
이는 **FFT 입력·내부 연산·최종 출력의 양자화를 포함한 경로가 Mel 로그에 미치는
영향의 관측**이며, 최종 출력 폭만의 영향이나 실제 음성 MFCC 정확도 수치가 아니다.
각 양자화 위치의 비교는 [FIXED_POINT_PRECISION_REVIEW.md](FIXED_POINT_PRECISION_REVIEW.md)를 따른다.

#### 참고 실험: 프레임별 "Q15 양자화 후 정수 shift" (채택하지 않음)

**[3판 정정 C10] 1·2판은 이 실험을 "block floating point"라고 불렀다. 실제 스크립트가
하는 일과 다르다.** `sweep_headroom.py` K절의 실제 동작은 다음과 같다.

| 항목 | 실제 스크립트(`sweep_headroom.py` K절) |
|---|---|
| 양자화 위치 | `xr = q15(base * peak * win)` — **먼저 `α = 1`로 양자화**한다 |
| 입력 배율 α | **1** (`u`를 그대로 Q15로). `α = 1/2`가 아니다 |
| 지수 적용 | `xs = clip(xr << s, -32768, 32767)` — **양자화된 정수를 왼쪽 shift** |
| shift 선택 | `pk << (s+1) <= 16383`을 만족하는 최대 `s`. `pk`는 **양자화된** peak |
| 큰 진폭 fallback | `np.clip(..., -32768, 32767)` saturation. 이 신호군에서는 `pk<<s <= 16383`이므로 **발동하지 않았다** |

즉 이것은 **양자화 후 shift**이며, 첫 양자화에서 이미 버린 정보를 되살리지 못한다.
**양자화 전에 지수를 고르는 진짜 BFP와는 다른 연산이다.** 두 방식의 차이는
[FIXED_POINT_PILOT.md](FIXED_POINT_PILOT.md) 3.1·3.3에서 측정했다 — 개발 음성에서는
거의 같지만 `small_alternating_1`에서 1.756e-04 대 1.477e+01로 갈리고,
양자화 후 shift만 입력 clipping **923 frame-samples / 1,385 단계 events**를 만든다.
이는 `pilot_03_fixed_accounting`에서 분리 집계한 값이며, 이전의 "1,385샘플" 표기는
width saturation과 대칭 clamp 이벤트를 샘플 수로 혼동했다. 원본 실행 기록은 보존한다.

아래 표는 그 **양자화 후 shift** 실험의 결과다. **FFT RTL은 수정하지 않는다.**

| 입력 peak | shift s | 상향 후 peak | 최대 \|Δlog\| (dB) | 평균 (dB) | overflow |
|---:|---:|---:|---:|---:|---:|
| 1.0 | 0 | 0.989 | 0.011 | 0.003 | 0 |
| 0.0625 | 3 | 0.495 | 0.036 | 0.007 | 0 |
| 0.01 | 5 | 0.316 | 0.040 | 0.011 | 0 |
| 0.003 | 7 | 0.379 | 0.019 | 0.006 | 0 |
| 0.0003 | 10 | 0.313 | 0.056 | 0.012 | 0 |

seed 5로 만든 400 프레임 무작위 잡음에서도 최악 band 오차 **0.046 ~ 0.181 dB**, overflow 0.

→ **이 합성 신호 조건에서는** 프레임별 지수가 -70 dBFS 수준까지 Mel 로그 오차를
0.2 dB 이내로 유지했다. **그러나 이것은 채택 근거로 충분하지 않다.** 실제 음성에서의 효과,
프레임마다 달라지는 shift가 delta/delta-delta와 CMVN에 주는 영향, 지수 전달 경로의
비용은 모두 미검증이다.

이 표의 Mel 수치에는 **Mel 계수 양자화 오차가 포함되어 있지 않다** — float64 필터뱅크를
그대로 썼다. 계수 양자화만의 기여는 따로 측정했고(Fw=16에서 `max|Δln E| = 2.294e-05` nats),
[FIXED_POINT_QUANTIZATION_PLAN.md](FIXED_POINT_QUANTIZATION_PLAN.md) 1.5에 Fw 12~20 표가 있다.

개발 음성 1개 전체(534프레임)와 합성·추가 입력 23개에 대한 실제 후보 비교는
[FIXED_POINT_PILOT.md](FIXED_POINT_PILOT.md)에 있다. 그쪽이 이 절의 합성 신호 실험을
대체하는 더 강한 근거다.

### 5.3 오버플로 한계 — 시험한 입력군 안에서

**재현 가능한 근거(seed 20261004, `sweep_clamp_check.py`, 총 13,168 프레임):**

| N | peak | 사각파(전 k, 결정론적) overflow | ±부호 패턴 500프레임 overflow |
|---:|---:|---:|---:|
| 256 | 1.000 | 19 / 256 | 20 / 500 |
| 256 | 0.975 | **0** | **0** |
| 512 | 1.000 | 32 / 512 | 24 / 500 |
| 512 | 0.975 | **0** | **0** |
| 1024 | 1.000 | 60 / 1024 | 26 / 500 |
| 1024 | 0.975 | **0** | **0** |

**관측된 사례와 입력 범위의 관계 (`-32768` 코드 포함 여부):**

**[3판 정정 C11]** 1·2판은 이 절에 "원인 특정"이라는 제목을 달았다. 과한 표현이다.
아래가 보이는 것은 **시험한 두 신호군에서 관측된 overflow가 입력 범위를
`[-32767, 32767]`로 바꾸자 모두 사라졌다**는 사실뿐이고, `-32768`이 유일한 원인이라는
일반 증명이 아니다.

| N | 입력 범위 | 사각파 overflow | ±부호 패턴 overflow |
|---:|---|---:|---:|
| 256 | `[-32768, 32767]` | 19 / 256 | 29 / 500 |
| 256 | `[-32767, 32767]` | **0** | **0** |
| 512 | `[-32768, 32767]` | 32 / 512 | 33 / 500 |
| 512 | `[-32767, 32767]` | **0** | **0** |
| 1024 | `[-32768, 32767]` | 60 / 1024 | 31 / 500 |
| 1024 | `[-32767, 32767]` | **0** | **0** |

Type-II stage 출력의 18비트 범위는 `[-131072, +131071]`이고, 네 개의 `-32768`이 부호
정렬되면 quarter 합이 정확히 `+131072`가 되어 wrap한다. 입력을 `[-32767, +32767]`로
clamp하면 **이 두 신호군에서** 관측된 모든 경우가 사라진다. 비용은 1 LSB다.

**주장의 범위(C7 정정):**

- 시험한 입력군은 **① 전 k full-scale 사각파(결정론적)**와 **② 무작위 ±A 부호 패턴**
  두 가지뿐이다. 둘 다 합성 적대 신호이고 실수 입력(`in_im = 0`)이다.
- peak 점은 **1.000과 0.975 두 점**만 재현 가능한 자료로 확인했다. 그 사이 구간과
  0.975 미만의 세부 점은 재현 자료 부족 로그(10.3)에만 있다.
- 유한 표본이므로 **부재의 증명이 아니다.** RTL이 포화가 아니라 wrap하고 피해가
  수백 LSB이므로, 입력 제한을 걸더라도 **`out_overflow`를 프레임별로 수집해야 한다.**

재현 자료가 없는(저장되지 않은 인라인 명령으로 생성된) 더 넓은 스윕에는
peak 0.70~0.99 전 구간 overflow 0, pre-emphasis+Hamming 잡음 1,000프레임 overflow 0,
복소 입력 한계 등이 포함되어 있다. 10.3에서 **재현 자료 부족**으로 표시한다.

복소 입력에 대해 재현 가능한 자료로 확인된 것은 **id 29(full-scale 복소 프레임에서
overflow 발생, RTL과 모델 일치)** 뿐이다. 안전 조건이 `|re|,|im| ≤ 1`이 아니라
복소 크기 기준이라는 분석은 10.3의 미재현 로그에 있다.
MFCC는 `in_im = 0`이므로 실용상 문제가 되지 않지만, 복소 묶기 기법을 쓰려면 다시 시험해야 한다.

---

## 6. MFCC 연결에 필요한 변경 사항

### 6.1 필수

| # | 항목 | 내용 |
|---|---|---|
| M1 | **출력 수용 설계** | 최종 `out_valid`를 매 cycle 받는 소비자를 두는 것이 **기본**이다. PL 안에서 power→Mel 누산으로 바로 받으면 충족된다.<br>소비자가 멈출 수 있다면 **출력 FIFO + 프레임 수락 제한**이 필요하다. 조건은 아래 M1-a~M1-e. 코어에 정지 포트가 없으므로(4.5) "배선"으로는 해결되지 않는다. |
| M1-a | FIFO 깊이 | 프레임의 출력은 **N beat 연속·정지 불가 버스트**다. 따라서 FIFO 깊이는 최소 **N complex word** 이상이어야 한다. **[3판 정정 C9] 구체적인 깊이와 credit 방식은 이 문서에서 확정하지 않는다.** 기록된 연속 실행에서 미완료 프레임이 최대 4개 겹치며(4.4), 필요한 깊이는 그 수만으로 정해지지 않는다. 후속 protocol 설계에서 확정·검증할 것. |
| M1-b | 수락 전 여유 공간 확보 | 프레임 **첫 샘플을 수락하기 전에** FIFO에 N word의 여유가 보장되어야 한다. 프레임 도중에 멈춰도 출력 버스트 길이는 줄지 않으므로, 게이팅은 **프레임 경계에서** 해야 한다. |
| M1-c | 예약 방식(순간 여유 확인만으로는 불충분) | 동시에 여러 프레임이 처리 중일 수 있으므로(4.4), `reserved = N × (수락됐지만 출력 버스트 미완료 프레임 수)`를 유지하고 **`DEPTH − occupancy − reserved ≥ N`**일 때만 새 프레임 첫 샘플을 수락한다. 순간 free space만 보면 두 프레임이 동시에 통과해 넘칠 수 있다. 예약은 해당 프레임의 마지막 beat가 FIFO에 들어간 시점에 해제한다. |
| M1-d | 다음 프레임 유입 제한 | 위 조건이 거짓이면 **새 프레임을 시작하지 않는다**(입력단에서 막는다). 소비자가 오래 멈추면 프레임이 입력단에서 거절되는데, 이는 관측 가능하고 안전하다. FIFO가 조용히 넘치게 두면 안 된다. 깊이 N이면 프레임마다 직전 버스트가 완전히 빠진 뒤 다음 프레임을 시작하므로 매번 `ST_BETWEEN`→`ST_DRAIN` 비용(약 N+25 cycle, 4.3)이 붙는다. MFCC 예산에서는 무해하다. |
| M1-e | 넘침 검출 | FIFO write-while-full을 별도 에러 플래그로 뽑고 프레임 상태에 포함한다. `bank_collision_error`는 이 상황을 **잡지 못한다**(4.5 (b) — 두 경로는 독립). |
| M2 | **입력 스케일과 clamp** | `in = round(α·u·32768)`, `in_im = 0`. 규격 초안의 `α = 1/2`(→ `|z| ≤ 0.975`)는 5.3의 두 신호군에서 overflow 0이었다. 양자화 결과를 **`[-32767, +32767]`로 clamp**해 `-32768` 코드를 배제한다. |
| M3 | **출력 배율 환산** | `P[k] = N·(out_re² + out_im²) / (α² · 2^30)`. `α=1/2`, `N=512` → `P[k] = (out_re²+out_im²)/2^19`. 적용 조건은 `FFT_SPEC_HANDOFF.md` 3장. |
| M4 | **설정 시퀀스** | 리셋 해제 후 idle에서 `cfg_log2_n=9` + 1 cycle `cfg_apply`를 반드시 1회 발행. |
| M5 | **handshake 준수와 `in_last`** | `in_valid && in_ready`에서만 진행하므로 수락될 때까지 데이터·`in_last` 유지. `in_last`는 n=511에 정확히 1. `input_frame_error`를 프레임 상태로 수집. |
| M6 | **`out_overflow` 수집** | 프레임 플래그로 받아 해당 프레임을 무효 처리하거나 재스케일 후 재계산. wrap이므로 무시 금지. |
| M7 | **계수 파일 배선** | `twiddle_1024_w16.mem`을 Vivado 프로젝트 소스로 추가하고 시뮬레이션 작업 폴더에도 배치. `ROM_FILE`은 **상대 파일명**이라 경로를 못 찾으면 ROM이 비고 설계가 조용히 틀린다. 합성 로그의 `$readmem ... read successfully` 확인을 절차에 넣을 것(2.6에서 2024.2 확인). |
| M8 | **FFT 바깥 단계 구현** | pre-emphasis, 프레임 분할(512/160), Hamming 윈도는 FFT에 **없다**. |

### 6.2 권장

| # | 항목 | 내용 |
|---|---|---|
| R1 | bin 게이팅 | `out_index ≤ 256`만 power/Mel로 보내고 나머지는 버린다. 재정렬 불필요(3.2). |
| R2 | 비트모델 공유 | `<검토 폴더 2020.2>/fft_model.py`는 RTL과 비트정확이 확인되었다(두 Vivado 버전). `software/fixed_model/`의 FFT 단계 기준으로 쓰면 중복 검증이 줄어든다. |
| R3 | N=512 전용 벡터 승격 | 기존 벡터는 N=16/1024뿐이다. 이번 N=512 시나리오(impulse/DC/정수 bin/경계 공백/오버플로 경계)를 `verification/`의 공통 벡터로 올릴 것. |
| R4 | 입력 공급기 | 프레임 내부 공백은 결과를 바꾸지 않으므로(4.2) 공급기 타이밍에 여유가 있다. 다만 **`in_ready` handshake는 반드시 구현**해야 하며 valid-only 공급은 안 된다. 경계 gapless를 억지로 맞출 필요는 없다(4.3). |
| R5 | 다른 크기 시험 보강 | 런타임 크기 변경을 쓸 계획이면 N=256/128/64/1024의 시나리오를 늘릴 것(현재 크기당 1~2개, 2.3). |

### 6.3 선택 (정확성 무관)

| # | 항목 |
|---|---|
| O1 | N=512 전용 축소(group 5개·1024 entry 재정렬/ROM이 과잉). `fft_r22sdf_core.sv`의 group 크기가 상수라 RTL 수정 필요. 자원 여유가 있어 우선순위 낮음 |
| O2 | 재정렬 bank를 BRAM으로(`ram_style = "block"`). 현재 LUTRAM 2,142, BRAM은 3/140만 사용 |
| O3 | DSP MREG 파이프라인(DRC DPOP-2 ×10). 100 MHz는 이미 만족하므로 전력·여유용 |
| O4 | 미사용 레거시 모듈 정리: `fft_r22sdf_core_n16/n64.sv`는 어디서도 인스턴스되지 않고, `r22sdf_stage.sv`, `r22sdf_group_quantize.sv`, `twiddle_addr_gen.sv`, `twiddle_rom.sv`, `complex_mult_4mul.sv`는 그 레거시 top에서만 쓰인다 |
| O5 | VHDL 변환 불필요: 기존 FFT는 전부 SystemVerilog다. `DEVELOPMENT_HANDOFF.md`가 적은 "기존 VHDL 모듈"은 참고 MFCC의 `get_frames.vhd`와 IP 쪽이며 FFT에는 해당 없음 |

---

## 7. 정확성·통합성 문제 목록 (우선순위)

| 등급 | ID | 내용 | 근거 | 대응 |
|---|---|---|---|---|
| 높음 | F-1 | **코어에 출력 정지 포트가 없다.** 하류 ready/stall/enable 입력이 포트 목록에 존재하지 않고, 재정렬 버퍼의 읽기측도 멈출 수 없다. 전역 stall은 다단 RTL 수정이며 미시도·미검증 | 4.5 (a) | M1 (FIFO + 수락 제한) |
| 높음 | F-2 | 오버플로가 포화가 아니라 **wrap**. 발생 시 오차 수백~수천 LSB | `fixed_round_shift.sv:69,72`; id 24/29 | M2, M6 |
| 높음 | F-3 | **AXI master가 정지 중 데이터·`tvalid`·`tlast`를 수락 시점까지 유지하지 않는다.** hold/skid 레지스터 없음 → beat 소실, TVALID 조기 하강(규약 위반), TLAST 소실로 프레임 경계 유실 | `fft_axi_stream_wrapper.sv:83-86,158-159` | M1, 또는 MFCC를 PL 내부 연결로 설계 |
| 높음 | F-4 | 조용한 프레임에서 출력이 양자화 바닥에 묻힌다(합성 신호 기준 -40 dBFS 이하에서 Mel 로그 오차 1 dB 초과, bin이 0이 되는 영역 존재) | 5.2 | M2 + 입력 정규화. BFP는 후보 |
| 중간 | F-5 | 출력 스케일이 `DFT/N`이라 기준 모델 정의와 2^19배 어긋난다(N=512, α=1/2) | 3.4 | M3 |
| 중간 | F-6 | 리셋 후 `cfg_apply` 없이는 입력이 영구히 수락되지 않는다. 통합 초기 오진 위험 | `fft_stream_top.sv:75`; D1/D2 | M4 |
| 중간 | F-7 | `ROM_FILE`이 상대 파일명. 경로를 못 찾으면 ROM이 비고 **타이밍은 통과하면서 결과만 틀린다** | `twiddle_rom_sync.sv:9,21` | M7 |
| 중간 | F-8 | 기존 TB가 입력 공백을 금지(`$fatal`)하고, **N=512·N=128은 한 번도 시뮬레이션되지 않았다** | `tb_fft_stream.sv:183,276`; `reports/phase5_runtime_results.csv`, `phase6_xsim_summary.csv` | 이번에 해소(2.3). R3로 회귀 고정 |
| 중간 | F-9 | 외부 정지로 인한 손실은 `bank_collision_error`가 **잡지 못한다**. 두 손실 경로가 독립인데 플래그가 하나뿐 | 4.5 (b) | M1-e |
| 낮음 | F-10 | 100 MHz 여유가 2024.2에서 +0.106 ns로 줄었다. PS7·AXI를 포함한 실제 블록 디자인에서 재확인 필요 | 2.6 | 9장 미검증 |
| 낮음 | F-11 | Phase 2 산술 검증 대상에 **런타임 데이터패스가 쓰지 않는 모듈**이 섞여 있다(`complex_mult_4mul`은 레거시 경로 전용) | `r22sdf_runtime_group_quantize.sv`는 DSP를 직접 기술 | 이번 비트정확 검증으로 보완(2.2~2.4) |
| 낮음 | F-12 | `cfg_error`가 다음 성공 설정까지 sticky. 상태 폴링 시 오해 가능 | `fft_stream_top.sv:152-157` | 문서화 |

### 스타일 선택 사항 (정확성 무관)

- `fft_r22sdf_core.sv`의 `reverse_index` case 문과 `bit_reverse_addr.sv`의 루프 구현이 기능 중복.
- `fft_stream_top`에 `core_out_index_unused`, `core_token_*_unused`, `reorder_core_ready` 등
  미사용 신호가 남아 있다. 특히 `reorder_core_ready`는 **연결될 포트가 없어서** 남은 것이므로
  주석으로 그 사실을 적어 두면 오해를 막는다.
- OOC DRC의 RTSTAT-10은 OOC top에서 출력 포트에 load가 없어 생기는 흐름 산물이다.

---

## 8. 규격 초안과의 대조 (`docs/MFCC_SPEC.md`, 2026-10-04 판)

이 대조는 **내가 MFCC_SPEC.md를 수정하지 않았다**는 전제에서의 제안이다.
반영안 전체는 `FFT_SPEC_HANDOFF.md`에 있다.

| 규격 초안이 남긴 항목 | 이번 결과 |
|---|---|
| "N=512의 비트정확·연속 프레임 시험과 누적 shift S는 별도 FFT 검토에서 확정"(`:121`) | **확정.** N=512 시나리오 24개 비트정확 일치, 연속 8프레임 공백 0, **S = log2 N = 9**. 2020.2·2024.2 결과 동일 |
| "N512 shift/순서/overflow/단독 drain 검증 필요"(`:139`) | **완료.** shift=9, 자연 순서, overflow 플래그 모델 일치, 단독 프레임 drain 정상 |
| `z = u/2`로 `|z| ≤ 0.975` 입력 제안(`:123`) | **조건부 확인.** 5.3의 두 적대 신호군(사각파 전 k, ±부호 패턴 500프레임)에서 peak 0.975 overflow 0. 실제 음성은 미시험. `[-32767, 32767]` clamp 추가 권장 |
| `F ≈ FFT(u)/(2·2^S)` 보정(`:128`) | **일치.** S=9 → `P[k] = (out_re²+out_im²)/2^19` |
| "기존 FFT wrap+sticky를 비트모델에 그대로 반영"(`:143`) | **확인.** wrap 맞음. `out_overflow`는 **프레임 단위**이고 프레임 간 누적은 아니다(3.6) |
| "downstream이 멈출 수 있으면 프레임 전체를 담을 버퍼와 overflow 검출을 둔다"(`:156`) | **방향은 맞고 조건을 보강한다.** 버퍼 깊이만으로는 부족하고 **프레임 수락 전 N word 예약 + 다음 프레임 유입 제한**이 필요하다(M1-b~M1-d). 또 코어에 정지 포트가 없어 "코어를 세우는" 대안은 RTL 수정이다(4.5) |
| "DMA S2MM을 먼저 시작하는 것만으로 임의 backpressure 지원이 증명되지 않는다"(`:156`) | **동의.** 판정 기준을 "정지 중 데이터·valid·last 유지 여부"로 바꾸면 **유지하지 않음**이 명확하다(4.5 (c)) |
| `fft_block.xci` float32 "실제 floating-point 이득은 미확인"(`:40`) | 이번 검토 범위 밖. 기존 FFT는 float IP의 대체가 **아니다**(0장) |

충돌은 없다. 보강이 필요한 지점은 3·6·7행이다.

---

## 9. 미검증 항목

재사용 판정은 이 범위를 전제로 한다.

1. **실제 보드 동작 전부.** 비트스트림, XSA, BSP, ELF, BOOT.bin, JTAG, UART,
   PS-PL DMA, 보드에서의 계수 회수 — 하나도 실행하지 않았다(2.7).
2. **Zynq 블록 디자인과 PS7 preset.** 신뢰할 수 있는 board file 미확보.
   **PS7·AXI를 포함한 100 MHz 타이밍 재확인도 미실행**(2024.2 OOC 여유 +0.106 ns, F-10).
3. **Vitis 애플리케이션 빌드·실행.**
4. **AXI 래퍼의 정지 시 손실 정량화.** sticky 비트 상승만 확인했고,
   어떤 beat가 어떻게 사라지는지 측정하지 않았다. 2024.2에서는
   `tb_fft_axi_stream_wrapper`를 **재실행하지 않았다**.
5. **전역 stall 또는 출력 FIFO의 구현·검증.** M1은 설계 제안이며 RTL이 없다.
6. **MATLAB 모델 재실행.** 설치 여부 미확인. 기존 NMSE 수치(-55.0781 dB 등) 미재현.
7. **100 MHz 외 클록 탐색.** 이번 요청 범위 밖(105/115/125 MHz 미실행).
8. **`fft_r22sdf_core` 단독 OOC.** `fft_stream_top`만 빌드했다.
9. **N = 8, 32.** 검증한 크기는 512/256/1024/128/64뿐이고 512 외에는 크기당 1~2 시나리오다(2.3).
10. **오버플로 무발생의 증명.** 5.3은 두 합성 적대 신호군의 유한 표본이다.
    구간 분석·형식 증명을 하지 않았다. peak 1.000과 0.975 사이 구간도 재현 자료가 없다.
11. **실제 음성 기반 MFCC 전체 경로.** power/Mel/log/DCT를 구현하지 않았다.
    5.2의 Mel 수치는 합성 잡음 + 자작 float64 필터뱅크이며
    **규격 문서의 필터뱅크·로그 하한과 맞추지 않았다.** BFP 채택 근거로도 불충분하다.
12. **BFP의 실제 음성 효과와 delta/CMVN 영향.** 미시험(`FFT_SPEC_HANDOFF.md` 7장 제안).
13. **다른 시뮬레이터 교차 검증.** XSIM만 사용(2020.2 + 2024.2).
14. **전력 측정.** 전혀 하지 않았다.
15. **재현 자료가 없는 수치.** 10.3 참고.

---

## 10. 근거 소스와 산출물

### 10.1 근거 소스 (읽기만 함)

루트: `D:\2610_MFCC\reference_code\previous_fft`

| 파일 | 확인한 내용 |
|---|---|
| `rtl/top/fft_stream_top.sv` | 최상위 배선, `in_ready` 게이팅, `reorder_core_ready` 미사용, 설정 상태기 |
| `rtl/top/fft_r22sdf_core.sv` | **포트 목록(정지 입력 없음)**, 상태기, token 스케줄, bit-reverse 인덱스, overflow 누적 |
| `rtl/r22sdf/r22sdf_runtime_group.sv` | group당 Type-I/II 활성 조건, 단독 Type-I의 `>>1` |
| `rtl/r22sdf/r22sdf_runtime_stage.sv` | SDF 지연·compute phase·`rotate_region`·tag 전파 |
| `rtl/r22sdf/r22sdf_runtime_group_quantize.sv` | DSP 누산, `>>15` 재양자화, group `>>2`, L=4 bypass |
| `rtl/r22sdf/r22sdf_runtime_twiddle_addr_gen.sv` | `branch_digit = [0,2,1,3]`, W1024 지수 환산 |
| `rtl/r22sdf/twiddle_rom_sync.sv` | 동기 ROM, `$readmemh(ROM_FILE)` |
| `rtl/common/fixed_round_shift.sv` | ties-to-even, **wrap + overflow 검출** |
| `rtl/r22sdf/bf_type1.sv`, `bf_type2.sv` | 성장 비트, `rotate_minus_j` |
| `rtl/reorder/natural_reorder_pingpong.sv` | 2-bank 재정렬, **읽기측 정지 입력 없음**, `bank_collision_error` |
| `rtl/reorder/bit_reverse_addr.sv` | 하위 `cfg_log2_n` 비트만 역순 |
| `rtl/top/fft_axi_stream_wrapper.sv` | TDATA 패킹, **hold 레지스터 없음**, sticky 상태 비트 |
| `tb/tb_fft_stream.sv` | 재실행한 기존 TB (입력 공백 금지 확인) |
| `tb/tb_fft_axi_stream_wrapper.sv` | 재실행한 기존 AXI TB (2020.2만) |
| `tb/vectors/`, `tb/expected/` | 재실행 입력·기대값 (N=16, N=1024) |
| `matlab/generated/twiddle_1024_w16.mem` | 사용한 twiddle ROM |
| `matlab/export_twiddle_rom.m` | `angle = -2*pi*k/N` (순방향 부호 근거) |
| `docs/interface.md`, `docs/open_issues.md`, `README.md` | 기존 주장과 제약 |
| `reports/phase6_xsim_summary.csv`, `phase7_timing_summary.csv`, `phase8_*` | 기존 수치와 비교 |
| `vivado/scripts/run_phase*.tcl`, `phase7_common.tcl` | 재현용 소스 목록·흐름 참조 (실행하지 않음) |

참고 MFCC(`D:\2610_MFCC\reference_code\github_mfcc`)에서 통합 제약 확인용:

| 파일 | 확인한 내용 |
|---|---|
| `FPGA source/.../MFCC.v` | 체인 구성, 전구간 float32 |
| `FPGA source/.../power_spectrum.v` | float32 `fft_block`(xfft), `|X|²/512`(`32'h44000000`), 257 bin |
| `FPGA source/.../get_frames.vhd` | `frame_length = 512`, `frame_step = 160` |
| `FPGA source/.../windowing.v` | `win_length = 512`, Hamming |
| `FPGA source/.../filterbanks.v` | 26 band, 257 bin |

### 10.2 산출물

**Vivado 2024.2: `<검토 폴더 2024.2>`**

| 경로 | 내용 |
|---|---|
| `logs/RESULTS_2024_2.txt` | **버전·명령·종료 코드·수치 비교·timing·자원 통합 기록** |
| `logs/source_hashes.txt` | RTL 20개 + ROM + TB의 SHA-256, 원본 대조 결과 |
| `logs/probe1_*.log`, `probe2_*.log`, `directed_*.log` | xvlog/xelab/xsim 로그 |
| `logs/run_sim_2024_2_console.log` | runner 콘솔 + 종료 코드 요약 |
| `logs/check_probe_2024_2.txt`, `check_probe2_2024_2.txt` | checker 출력(종료 코드 0) |
| `logs/ooc_fft_stream_top_100mhz.log`, `logs/ooc_console.log` | 구현 로그 |
| `work/synth_fft_stream_top_10.000/post_route_*.rpt`, `post_route.dcp` | 구현 리포트·체크포인트 |
| `work/probe/`, `work/probe2/`, `work/directed/` | 자극·출력 CSV |
| `run_sim_2024_2.ps1`, `run_ooc_impl.tcl`, `check_probe_fixed.py`, `fft_model.py` | 실행 스크립트 |

**Vivado 2020.2 (보존): `<검토 폴더 2020.2>`**

| 경로 | 내용 |
|---|---|
| `logs/phase6_rerun_*.log`, `phase8_rerun_*.log` | 기존 TB 재실행 |
| `logs/probe_*.log`, `probe2_*.log`, `directed_*.log` | 신규 TB 실행 |
| `logs/check_probe.txt`, `check_probe2.txt` | **수정 전** checker 출력(후자는 `KeyError`로 종료) |
| `logs/check_probe_fixed_r1_2020_2.txt`, `_r2_2020_2.txt` | 수정 checker 재판정(종료 코드 0) |
| `logs/check_probe_fixed_negative_controls.txt` | checker 검출력 음성 대조(종료 코드 1) |
| `logs/sweep_range.txt`, `sweep_range2.txt`, `sweep_headroom.txt` | 수치 분석(스크립트 보존됨) |
| `logs/sweep_headroom_band.txt` | **재현 자료 부족** — 10.3 |
| `logs/sweep_clamp_check.txt` | 축소 재현(seed 20261004, 스크립트 보존됨) |
| `logs/post_route_*.rpt`, `ooc_*.log` | 2020.2 구현 결과 |
| `fft_model.py` | RTL만 읽고 작성한 독립 비트정확 모델 |
| `check_probe_fixed.py`, `sweep_clamp_check.py` | 이번에 추가/수정한 도구 |
| `src/tb/tb_mfcc_fft_probe.sv`, `tb_mfcc_fft_directed.sv` | 신규 테스트벤치 |

### 10.3 재현 자료 상태 (C8 정정)

| 로그 | 생성 스크립트 | 상태 |
|---|---|---|
| `sweep_range.txt` | `sweep_range.py` | 보존 — 재현 가능 |
| `sweep_range2.txt` | `sweep_range2.py` | 보존 — 재현 가능 |
| `sweep_headroom.txt` | `sweep_headroom.py` | 보존 — 재현 가능 |
| `sweep_clamp_check.txt` | `sweep_clamp_check.py` (seed 20261004) | 보존 — 재현 가능 |
| **`sweep_headroom_band.txt`** | **없음** (저장되지 않은 인라인 heredoc 명령) | **재현 자료 부족** |
| `check_probe.txt`, `check_probe2.txt` | `check_probe.py`, `check_probe2.py` | 보존. 단 후자는 예외 종료(2.5) |

`sweep_headroom_band.txt`에만 있는 수치(peak 0.70~0.99 전 구간, pre-emphasis+Hamming
1,000프레임, 복소 입력 한계 등)는 **근거로 쓰지 않는다.** 결정적 결론 세 개
(실수 full-scale에서 overflow 발생 / peak 0.975에서 0건 / `[-32767,32767]` clamp가
관측 사례 제거)만 `sweep_clamp_check.py`로 축소 재현했다(5.3).
재현 결과에서 결정론적 사각파 계수(19/256, 32/512, 60/1024)가 원래 로그와 일치하므로,
재구성이 원래 실험과 같은 동작을 한다는 교차 확인은 된다.

### 10.4 재현 명령

```bash
# --- Vivado 2024.2 (이 프로젝트의 기준 버전) ---
powershell -File <검토 폴더 2024.2>\run_sim_2024_2.ps1
cd <검토 폴더 2024.2>
python check_probe_fixed.py work\probe  "2024.2 probe"
python check_probe_fixed.py work\probe2 "2024.2 probe2"
"C:\Xilinx\Vivado\2024.2\bin\vivado.bat" -mode batch -nojournal ^
  -log    <검토 폴더 2024.2>\logs\ooc_fft_stream_top_100mhz.log ^
  -source <검토 폴더 2024.2>\run_ooc_impl.tcl ^
  -tclargs fft_stream_top 10.000

# --- Vivado 2020.2 (보존된 증거의 재현) ---
powershell -File <검토 폴더 2020.2>\run_phase6_rerun.ps1
powershell -File <검토 폴더 2020.2>\run_phase8_rerun.ps1

# --- 도구 버전과 무관한 Python 분석 ---
cd <검토 폴더 2020.2>
python validate_model.py
python sweep_range.py
python sweep_range2.py
python sweep_headroom.py
python sweep_clamp_check.py
```
