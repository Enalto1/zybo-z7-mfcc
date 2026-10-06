# FP32 MFCC OOC synthesis and routing

`scripts/run_fp32_ip_synth.py`는 Vivado 2024.2로 `fp32_mfcc` 전체 top을
`xc7z020clg400-1`에 합성·배치·배선한다. 모든 결과는 새 `build/fp32_hw/synth_*`에
저장한다. 기존 run은 덮어쓰지 않는다.

```powershell
& D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe -B `
  D:\2610_MFCC\project\scripts\run_fp32_ip_synth.py `
  --run-id synth_reproduce_01 --ip-run D:\2610_MFCC\build\fp32_hw\ip_04
```

## 고정하는 입력과 실행

- authored RTL 6개, coefficient generator, 실행 Python/Tcl을 `source/`에 복사하고 SHA256 기록.
- frozen C `reproduce_01`의 binary32 계수로 ROM `.mem`을 생성. 음성 평가 자료를 읽지 않는다.
- 모든 생성 `.mem` 해시와 frozen RTL의 literal `INIT_FILE` 기본값·인자를 기록한다.
  참조한 ROM이 빠졌거나 source가 snapshot 중 바뀌면 Vivado 실행 전에 실패한다.
  합성 후에는 실제 참조 ROM 각각의 `$readmem ... is read successfully` 로그를 요구한다.
- 지정 IP run의 `ip_manifest.json` 및 모든 기록된 output hash를 확인한다.
- `import_ip`로 XCI를 새 프로젝트에 복사하고 해당 run 안에서 제품 생성 및 IP 합성 수행.
- top 합성은 `-mode out_of_context`, 이후 `opt_design`, `place_design`,
  `phys_opt_design`, `route_design` 순서다.
- Vivado 2024.2 run 속성은 `{STEPS.SYNTH_DESIGN.ARGS.MORE OPTIONS}`이며 공백이 있다.

I/O buffer·보드 pin·PS7·AXI/DMA는 이 top에 포함하지 않는다. Bitstream 생성,
보드 연결·실행 및 전력 측정을 하지 않는다. 수치 simulation도 이 script 범위 밖이다.

## Timing의 적용 범위

clock period는 10 ns이며 OOC clock source를 `BUFGCTRL_X0Y0`으로 가정한다.
이는 실제 PS FCLK 연결이나 보드 clock 회로 검증 결과가 아니다. 합성 후 이 site가
target part에 존재하는지 확인한다. 외부 input/output delay와 partition pin은 지정하지
않으므로 경계 I/O의 timing을 통과했다고 표현하지 않는다.

내부 경로의 WNS/TNS/WHS와 `unconstrained_internal_endpoints`, 실제 route 상태를
함께 읽는다. OOC DRC는 일부 connectivity 검사를 생략한다는 Vivado 경고도 보존한다.
전체 시스템 통합 시 실제 clock source, 외부 인터페이스 제약으로 다시 구현해야 한다.

## 보존된 실행

| 실행 | RTL/IP | 결과 |
|---|---|---|
| `synth_01` | CE 이전 / `ip_03` | RTL 합성 전 Tcl option parsing 실패 |
| `synth_02` | CE 이전 / `ip_03` | 존재하지 않는 `MORE_OPTIONS` 속성 지정으로 실패 |
| `synth_probe_01` | 도구 질의 | 실제 run property와 synthesis help 확인 |
| `synth_03` | CE 이전 / `ip_03` | OOC route 완료. HD.CLK_SRC 누락 경고가 있어 timing 참고용 |
| `synth_04` | CE 이전 / `ip_03` | clock 위치 가정 명시, OOC route 완료 |
| `synth_ce_01` | CE 이후 / `ip_04` | 2026-10-04 17:58:26 KST OOC route 완료, exit 0 |
| `synth_compact_01` | compact Mel / `ip_04` | 2026-10-04 18:22:16 KST OOC route 완료, exit 0 |

`synth_04`의 조건부 내부 timing은 WNS +1.016 ns, WHS +0.011 ns, TNS 0이다.
LUT 4,296 / FF 7,786 / BRAM 15타일 / DSP 26, latch 0,
routable net 10,758개가 모두 배선됐고 routing error는 0이었다.
해당 숫자는 이후 CE RTL의 결과로 재사용하지 않는다.

## Dense Mel CE 기준 구현 결과

`D:\2610_MFCC\build\fp32_hw\synth_ce_01`에서 측정한 Vivado 구현 보고 값이다.

| 항목 | 합성 후 | 배치·배선 후 | target 전체 |
|---|---:|---:|---:|
| LUT | 5,116 | 4,353 (8.18%) | 53,200 |
| FF | 8,594 | 7,796 (7.33%) | 106,400 |
| BRAM tile | 15 | 15 (10.71%) | 140 |
| DSP48E1 | 28 | 26 (11.82%) | 220 |
| latch | 0 | 0 | — |

BRAM은 RAMB36 8개와 RAMB18 14개다. 배치·배선 후 LUT 기반 distributed RAM은 0개,
LUT shift register는 432개다. IOB와 BUFG는 OOC 내부에 각각 0개다.

10 ns 제약과 `BUFGCTRL_X0Y0` clock 위치 가정에서 WNS **+2.448 ns**, TNS **0 ns**,
WHS **+0.021 ns**, THS **0 ns**, pulse-width slack **+4.020 ns**였다.
최대/최소 delay endpoint 20,239개 중 실패 0개이며, routable net 10,820개를 모두
배선했고 routing error는 0개다. `no_clock`, `unconstrained_internal_endpoints`,
combinational loop, latch loop는 모두 0이다. 입력 22개·출력 109개에는 외부 delay가 없다.
따라서 **명시된 OOC 가정 아래 내부 100 MHz 경로 통과**로 표현한다.

Critical warning/error 및 inferred-latch 메시지는 top·IP 합성 로그와 통합 log에서
발견되지 않았다. DRC를 warning-free라고 표현하지 않는다. 배선 후 남은 항목은
`PDCN-1569` 46건(모두 vendor log IP 내부 LUT equation pin), `ZPS7-1` 1건(PS7 없는
OOC top), `AVAL-4` advisory 3건이다. XPM의 unused-port/register 제거 및 collision
메시지 설정 경고, 경계 포트의 `HD.PARTPIN_LOCS` 미지정 경고도 원문 로그에 보존됐다.
ROM `window.mem`, `mel.mem`, `dct_cosine.mem`, `dct_scale.mem` 네 파일을 성공적으로
읽었다는 합성 로그를 확인했다. 이 결과로 수치 정확도·전력·실보드 성능을 주장하지 않는다.

완료 시 `source_changed_after_snapshot=[]`였고 authored RTL 6개, generator, flow의
모든 hash가 당시 작업 파일과 같았다. 수치 검증 상태는 해당 simulation manifest를 별도로 본다.
배선 설계는 `fp32_mfcc_routed.dcp`로 열고, 원 project는 `project/fp32_mfcc.xpr`에 있다.

## 최신 compact Mel 구현 결과

`D:\2610_MFCC\build\fp32_hw\synth_compact_01`은 같은 `ip_04`를 사용한다.
Dense CE와 vendor IP manifest SHA256이 같고, RTL 6개 중 변경된 파일은 backend 하나다.
Mel의 positive nonzero 계수 459개를 512×32 ROM에 저장하고, 26개 필터의 bin/address
descriptor를 32×32 ROM에 저장한다. Dense `mel.mem`은 이전 실행과 같은 SHA256으로
계속 생성하며 추적 자료로만 보관한다. 생성기의 전체 dense 복원 및 MAC word 순서
검사 mismatch는 0이다. 이 검사는 RTL 수치 simulation의 대체물이 아니다.

| 동일 단계 비교 항목 | Dense CE | Compact Mel | 차이 |
|---|---:|---:|---:|
| 합성 LUT | 5,116 | 5,146 | +30 |
| 합성 FF | 8,594 | 8,599 | +5 |
| 합성 DSP | 28 | 28 | 0 |
| 배선 LUT | 4,353 | 4,379 (8.23%) | +26 |
| 배선 FF | 7,796 | 7,801 (7.33%) | +5 |
| 배선 BRAM tile | 15 | 8 (5.71%) | −7 |
| 배선 DSP48E1 | 26 | 26 (11.82%) | 0 |
| 배선 latch | 0 | 0 | 0 |

Compact의 BRAM은 RAMB18 16개, RAMB36 0개다. LUT distributed RAM 0개,
LUT shift register 433개다. 같은 OOC 가정과 10 ns 제약에서 WNS **+0.942 ns**,
TNS **0 ns**, WHS **+0.023 ns**, THS **0 ns**, pulse-width slack **+4.020 ns**이다.
10,835개 routable net 모두 배선됐고 routing error 0, 내부 unconstrained endpoint 0이다.
외부 delay 없는 입력 22개·출력 109개라는 범위와 DRC 46+1 warning/3 advisory는 동일하다.
BRAM은 줄었지만 timing slack이 개선됐다고 주장하지 않는다.

`rom_init_files_confirmed`에 window, compact Mel, descriptor, DCT cosine, DCT scale
다섯 파일의 실제 합성 load가 기록됐다. Dense Mel load는 없으며 해당 파일도
`coefficient_file_hashes`에 포함됐다. 완료 시 source 변경은 없었다.

### DSP 단계 차이의 직접 확인

`build/fp32_hw/resource_delta_01`은 두 구현의 합성·배선 DCP 4개를 읽어 생성한
hierarchy 보고서와 실제 `DSP48E1` cell 목록이다. 입력 DCP hash가 조회 후에도
그대로임을 확인했다. `dsp_comparison.json`의 cell 집합은 dense/compact 간 동일하다.

| 인스턴스 | Dense 합성 | Dense 배선 | Compact 합성 | Compact 배선 |
|---|---:|---:|---:|---:|
| Backend FFT | 12 | 12 | 12 | 12 |
| Backend LN | 4 | 4 | 4 | 4 |
| Backend MUL | 2 | 2 | 2 | 2 |
| Backend ADD/SUB | 2 | 2 | 2 | 2 |
| Preemphasis MUL | 2 | 2 | 2 | 2 |
| Preemphasis ADD/SUB | 2 | 2 | 2 | 2 |
| Window MUL | 2 | 2 | 2 | 2 |
| Window ADD/SUB | 2 | 0 | 2 | 0 |
| 합계 | 28 | 26 | 28 | 26 |

제거된 DSP 두 개는 두 구현 모두 `U_WINDOW/U_ALU/U_ADDSUB` 내부에 있었다.
`fp32_window.sv`는 ALU 요청을 MUL opcode `2'b00`으로 고정하므로 사용하지 않는
ADD/SUB 경로 제거에 해당한다. Compact 전환으로 DSP가 늘어난 것이 아니다.
FF 증가는 hierarchy의 backend에서만 +5로 확인됐고 다른 주요 모듈 FF는 동일하다.
LUT의 세부 mapping 변동 원인을 추가 추정하지 않는다.

## 근거 파일과 해시 해석

각 run의 `freeze.json`은 실행 직전 source/IP/계수 해시와 정확한 명령을 기록한다.
`run_manifest.json`은 종료 코드 및 실행 중 현재 소스의 변경 여부를 추가한다.
`artifact_manifest.json`은 로그·보고서·DCP를 포함한 산출물 해시다.

`reports/utilization_routed.txt`, `timing_routed.txt`, `route_status.txt`,
`drc_routed.txt`를 최종 구현 보고서로 사용한다. `utilization_synth.txt`와
`utilization_hierarchical.txt`는 합성 시점 값이다. 최적화 때문에 두 자원 수가 다를 수 있다.

후속 testbench/script/document 변경만으로 RTL 합성 결과가 바뀌지는 않는다.
결과를 현재 소스에 적용할 때 `hardware/fp32/rtl/*.sv` 6개와 생성 계수 및 IP 해시가
같은지 별도로 확인한다. 서로 다른 RTL/IP revision의 수치 검증·합성·timing 결과를
하나의 완료 상태로 섞지 않는다.
