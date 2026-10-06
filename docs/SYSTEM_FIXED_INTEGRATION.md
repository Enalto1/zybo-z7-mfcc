# ZYBO Z7-20 fixed MFCC PS/PL 시스템 통합

2026-10-05 KST 완료. Vivado 2024.2 / `xc7z020clg400-1`에서 새 시스템의 합성,
배치배선, 타이밍 검사, bitstream/XSA 생성과 PS7 버스 모델 시뮬레이션을 통과했다.
그 XSA에서 새 BSP와 ARM ELF도 생성했다. **실제 보드 다운로드·실행은 NOT_RUN,
기존 수치 정확도는 NOT_ACCEPTED**다. 구현 성공으로 이 두 상태를 대체하지 않는다.

최종 하드웨어 실행은 `D:/2610_MFCC/build/system_fixed/s06`, ARM 실행은
`D:/2610_MFCC/build/arm_accel/a06`이다. 실패 실행과 기존 발행 자료도 보존했다.
커밋·푸시·보드 접속은 수행하지 않았다.

## 범위와 고정된 기준

새 시스템 RTL/Tcl은 `hardware/system`, 테스트벤치와 감사는 `verification/system`,
전송 드라이버와 데모는 `software/arm_accel`, 호스트 검사는 `verification/arm_accel`,
전용 실행기는 `scripts/run_fixed_system.py`와 `scripts/build_arm_accel.py`다.
기존 PS-only 플랫폼, `software/arm_fixed`, `software/c_fixed`, FP32와 공통 기준값은
수정하지 않았다. 기존 fixed RTL/계수는 아래 검증 snapshot과 SHA가 일치한다.

| 기준 | 고정 경로 / SHA-256 |
|---|---|
| 수치 계약 | `D:/2610_MFCC/build/fixed_contract/v2_pcm16_mfcc40_20261004_r2/contract.json` |
| 계약 SHA | `283fff8abec22b3455a0219e37d27ac9c090ed2e74be3f7256677fb0cf4d6e3e` |
| 정수 모델 | `D:/2610_MFCC/build/fixed_full_model/integer_02_20261004` |
| 기존 전체 fixed RTL/OOC | `D:/2610_MFCC/build/fixed_full_rtl/full_616_repro_20261004_07` |
| PS-only 설정 비교 기준 | `D:/2610_MFCC/build/arm_platform/reproduce_01_platform` |
| Digilent board source commit | `36f34ab687b7fa9c778b779d027f3bce63b3ace9` |

계약의 437개 산출물, 원본 FFT 출처/hash, fixed RTL과 계수, 입력/정답 벡터를
검사했다. 개발 음성 534프레임, 기존 합성 17개 입력, 추가 합성 6개 입력을 모두
사용해 총 24개 입력·616프레임을 검증했다. 평가 음성 20개는 사용하지 않았다.

## 전송·제어 구조

ARM CPU0는 DDR에 저장된 PCM을 `M_AXI_GP0 → AXI Interconnect → fixed_accel_top`
경로로 보낸다. `mfcc_mmio`가 AXI4-Lite와 기존 `mfcc_fixed_top`의 ready/valid를
연결한다. 주소는 **0x43C00000..0x43C0FFFF**, 데이터는 32비트 little-endian,
정렬된 full-word 접근이다. DMA·IRQ 없이 polling으로 전송과 회수를 진행한다.
보드 대역폭이나 성능 향상은 아직 측정하지 않았다.

입력은 PCM signed16을 쓰기 데이터의 하위 16비트에 담는다. 출력은
**signed40/F24**를 64비트로 부호 확장한 raw 정수이며, frame32, 계수 index 0..12,
signed BFP8, last/error를 함께 읽는다. 프레임 오름차순, 각 프레임의 계수 0..12
순서이고 last는 계수 12다. BFP는 진단 metadata이며 최종 MFCC에 다시 곱하지 않는다.
프레임 길이 512, hop 160, 불완전 tail 폐기는 기존 계약 그대로다.

ARM은 SAMPLE_COUNT를 설정하고 START한 뒤 **출력을 먼저 회수·POP하면서 PCM을 공급**한다.
입출력 holding register는 각각 한 개이므로 전 입력을 먼저 쓰고 나중에 출력만
읽는 절차는 긴 clip에서 막힌다. 최대 입력은 262144 samples이며 최대 1636프레임,
21268개 출력 record다. DONE 때 마지막 출력이 slot에 남을 수 있어 모두 POP한 후
written/consumed/captured/popped와 소프트웨어 개수를 함께 검사한다.

| 기능 | offset / 의미 |
|---|---|
| 식별 | 0x00 ID, 0x04 ABI, 0x40 CORE_ID, 0x44 FORMAT, 0x60 계약 tag |
| 제어 | 0x08 START=1 / ABORT=2 / CLEAR=4; 명령은 단독 사용 |
| 상태 | 0x0C busy / done / error / PCM-ready / output-valid |
| 입력 | 0x10 sample count, 0x14 PCM |
| 출력 | 0x18 low32, 0x1C high32, 0x20 frame, 0x24 metadata, 0x28 POP |
| 진단 | 0x2C sticky errors, 0x30..0x3C 전송 counter, 0x58/0x5C busy cycles |

전체 필드·오류 6종·SLVERR·reset/ABORT 우선순위는
[REGISTER_CONTRACT.md](../hardware/system/REGISTER_CONTRACT.md)가 기준이다.
독립 AW/W 수락과 B/R 응답 보류를 처리하며, ABORT는 이미 수락해 보류 중인 AXI
응답을 취소하지 않는다. reset은 진행 중 거래·clip·valid를 폐기한다.

원본 FFT에는 ready가 없다. 기존 코어의 예약 제어, frame/power BRAM과 출력
backpressure 처리를 유지했으며 FFT를 임의 정지시키지 않았다. 새 MMIO는 프레임
FIFO를 추가하지 않아 RAM 전체 reset이나 배열 전체 next 복사가 없다.

## clock/reset·PS 설정·RTL 규칙

PS7 FCLK0의 **100 MHz 단일 PL 도메인**에 GP0 ACLK, interconnect와 가속기를 연결했다.
FCLK_RESET0_N은 active-low로 `proc_sys_reset`에 들어가 동기 해제된 peripheral
aresetn을 만든다. 신규 MFCC 제어는 `posedge`에서 처리하는 동기 active-low reset이다.
실제 생성된 reset 극성·연결과 reset 후 재시작은 PS7 시뮬레이션에서 검사했다.
외부 인터페이스는 DDR/FIXED_IO뿐이며 PL 핀이나 별도 clock crossing은 없다.

PS 설정 감사는 이전 PS-only의 891개 속성을 비교했다. 변경 13개는 GP0/FCLK0/reset0
관련 설정 11개와 생성 이름/CRC 2개뿐이다. DDR/DCI 145개, MIO/bank 252개,
CPU/shared PLL 14개와 나머지 보호 설정의 hash가 같았다. IO PLL 1000 MHz를 유지하고
FCLK0 분주 5×2를 사용한다. board preset의 4개 파일 hash도 보존했다.

`mfcc_mmio.sv`는 `always_ff` 1개와 `always_comb` 1개의 2-process 구조다.
신규 RTL에 for/generate-for, 사용자 function/task/initial, 래치는 없다.
`fixed_accel_top.sv`는 규약상 배선 전용 top이며 불필요한 순차 블록을 넣지 않았다.
Vivado IP 패키저와 `default_nettype none` 합성에 맞춰 외부 입력은 명시적 4-state
`input wire`, 내부·출력은 `logic`를 사용했다. 산술이나 E-FFT 예외 범위는 바꾸지 않았다.

Vivado 2024.2의 직접 SystemVerilog BD module-reference 제한을 해결하기 위해
별도 프로젝트에서 `mfcc.local:user:fixed_accel:1.0`으로 패키징한다. 실행 폴더의
`design/ip_repo`에 RTL·계수를 복사하고 SHA를 비교한다. 합성·시뮬레이션 fileset은
각각 26개 파일이며 두 계수 memory를 모두 포함한다. 최종 프로젝트에는 중복된
독립 RTL 정의를 넣지 않는다. 패키징·BD·합성·구현 Tcl과 재생성 Tcl을 모두 보존했다.

## 실제 검증 결과

| 검증 수준 | 결과와 범위 |
|---|---|
| 기존 fixed 전체 정수/OOC | 이전 `full_616_repro_20261004_07`의 616프레임 PASS. 새 PS 통합과 별도 근거 |
| 새 MMIO 단위 | fake core로 14개 시험 PASS, 오류 6종과 동시 사건 우선순위 포함 |
| AXI + 실제 fixed core | 24개 입력 / 616프레임 / MFCC 8008개, mismatch 0 |
| 실제 생성 PS7 버스 모델 | GP0/interconnect/reset/core 경유 PCM 672개 → 2프레임 / MFCC 26개 PASS |
| 전체 시스템 합성 | PASS, 새 MMIO 경고 0, 래치 0 |
| 전체 시스템 배치배선·타이밍 | PASS, 100 MHz, setup/hold/pulse-width 위반 endpoint 0 |
| bitstream·XSA | 생성 PASS, XSA 내부 bitstream과 외부 bitstream SHA 동일 |
| ARM 호스트 검사 | mock MMIO 968295개 검사 PASS |
| 새 XSA → BSP/ELF | BUILT_NOT_BOARD_RUN, ARM GCC 13.3 엄격 경고 검사 PASS |
| 실제 보드·ARM 명령 실행 | NOT_RUN |
| 수치 정확도 | NOT_ACCEPTED 유지 |

최종 RTL의 AXI/단위 시뮬레이션은 `system_full_20261005_05`에서 실행했다.
그 실행의 구현만 Windows 경로 제한으로 실패했다. `s06`은 RTL/TB/모든 벡터와
검증 산출물 SHA가 완전히 같은 것을 검사한 뒤 이 두 시뮬레이션 결과를 재사용했다.
`s06`에서 새로 수행한 것은 전체 합성·배치배선·bitstream/XSA와 생성 PS7 시뮬레이션이다.
재사용 출처와 manifest SHA는 `s06/run_manifest.json`의 `simulation_source`에 있다.

AXI 전체 시험은 14509321클록, read 4034486회, write 115860회다. R stall 1403304,
B stall 61344, core stall 4946클록과 5000클록 출력 보류를 포함한다. 입력 gap,
연속 프레임, 빈/짧은 입력, 마지막 프레임 배출, frame/index/BFP/last·부호 확장,
독립 AW/W reset, FFT 진행 중 reset, PCM 173개 후 ABORT와 정상 재시작을 검사했다.
단위 시험은 즉시 empty-done, 마지막 출력 capture와 done 동시 발생, ABORT 중 R
보존, CLEAR/ABORT와 잘못된 read의 동시 오류 우선순위도 확인한다.

PS 시험은 실제 생성된 PS7 VIP GP0, AXI interconnect/protocol converter,
`proc_sys_reset`과 fixed core를 사용했다. 10 ns clock, read 6731회/write 738회,
71588클록이며 reset 2개·오류 2개·empty 1개·ABORT 1개·출력 보류 2개 검사를 포함한다.
음수 PCM 321개와 음수 MFCC 17개도 통과했다. 이는 버스 모델 시뮬레이션이며
ARM 명령어 실행이나 물리 DDR timing 검증이 아니다.

## 전체 시스템 자원·BRAM·타이밍

`s06/reports/utilization_route.rpt`, `timing_route.rpt`, `clocks.rpt`,
`bram_cells.txt`, `drc_route.rpt`가 최종 근거다. 이전 OOC 자원·타이밍과 혼용하지 않는다.

| 항목 | 전체 시스템 route 결과 |
|---|---:|
| Slice LUT | 6525 / 53200 (12.27%) |
| FF | 3414 / 106400 (3.21%) |
| DSP | 40 / 220 (18.18%) |
| BRAM tile | 12.5 / 140 (8.93%) |
| 래치 | 0 |
| Clock | PS7 FCLKCLK[0]에서 전파된 `clk_fpga_0`, 10.000 ns |
| Setup WNS | +0.506 ns |
| Hold WHS | +0.030 ns |
| Pulse width slack | +3.750 ns |
| no_clock / unconstrained internal endpoints | 0 / 0 |

합성 시점은 LUT 6893 / FF 3492이며 위 표는 최적화·route 후 값이다.
BRAM primitive는 RAMB36E1 9개와 RAMB18E1 7개로 실제 매핑됐다.

| BRAM 용도 | RAMB36 / RAMB18 |
|---|---:|
| 전처리·window | 0 / 2 |
| FFT twiddle | 3 / 0 |
| FFT 자연 순서 ping-pong frame | 2 / 1 |
| Mel 계수 | 4 / 1 |
| Power | 0 / 2 |
| DCT 입력 log 저장 | 0 / 1 |

route DRC는 **Warning 60개, Error/Critical 0개**다. DSP pipeline 권고
DPIP-1 11개, DPOP-1 9개, DPOP-2 30개와 LUT pairing PDRC 경고 10개를 보존했다.
fixed 가속기 합성 경고 217개는 경로를 정규화하면 기존 fixed 기준 실행과 같으며
새 MMIO 경고는 없다. 그중 메모리 register 매핑 경고 4개는 1-word FFT 지연단이다.
큰 frame buffer의 BRAM 추론 실패가 아니다.

공식 board preset의 negative DQS skew 관련 PSU 경고와 vendor VIP의
`wrp_strb[128]` 범위 경고도 로그에 남아 있다. 테스트한 PS 거래는 4-byte 접근이다.
경고를 숨기거나 preset을 임의 보정하지 않았다. 실물 board revision과 DDR 동작은
후속 보드 검증 대상이다.

## ARM BSP·ELF와 감사

`a06`은 아래 새 `s06` XSA를 입력으로 standalone BSP를 새로 생성했다.
과거 PS-only BSP는 최종 링크 근거가 아니다. ELF는 ARM ELF32 little-endian EABI5,
Cortex-A9 hard-float/VFPv3, 223984 bytes다. GCC 13.3에서 `-Werror`,
`-Wconversion`, `-Wsign-conversion`을 적용했다. entry는 0x00100000, 모든 LOAD
segment의 최대 끝은 0x0021C4D0으로 예약 DDR [0x00100000, 0x02100000) 안에 있다.
BSP의 가속기 주소 0x43C00000..0x43C0FFFF와 UART1도 검사했다.

드라이버는 Device MMIO·DSB/ISB, drain 우선 polling, 유한 timeout/max-polls,
순서·부호·metadata·counter 검사와 ABORT 복구를 제공한다. 데모 ELF 내부의
PCM 512개와 정답 MFCC 13개가 발행 계약과 같은 것을 binary에서 직접 검사했다.
외부 PCM용 DDR buffer/layout와 control/status도 준비했다. 실제 주소·사용 절차는
[ARM README](../software/arm_accel/README.md) 및 `a06/build_manifest.json`에 있다.

| 독립 감사 | 결과 |
|---|---|
| `D:/2610_MFCC/build/system_fixed/s06_audit_01.json` | PASS 1368검사; 산출물 614개 SHA, 수치 계약, RTL/계수/벡터, 시뮬레이션 출처, BRAM/타이밍/DRC, bit/XSA |
| `D:/2610_MFCC/build/system_fixed/s06_ps_audit_01.json` | PASS; PS 891개 속성, 허용 변경 13개, preset·DDR·MIO·CPU 보존 |
| `D:/2610_MFCC/build/arm_accel/a06_audit.json` | PASS 2914검사; 산출물 1424개 SHA, ELF ABI·LOAD·심벌·DDR descriptor·내장 PCM/정답 |

ARM의 SLVERR는 Cortex-A9 data-abort를 일으킬 수 있으며 이 드라이버는 해당
exception handler를 설치하지 않는다. polling timeout은 MMIO가 반환되는 경우에
적용된다. 응답이 없는 AXI 거래 자체의 중단이나 물리 bus-error 복구를 호스트
callback 검사로 검증했다고 주장하지 않는다. busy-cycle은 PS가 만든 stall도
포함하므로 PL 순수 연산 시간이나 보드 실측 성능이 아니다.

## 고정 산출물과 SHA-256

아래 경로는 모두 절대 경로다. XPR만 옮기지 말고 IP repository와 source/report를
포함한 실행 폴더를 함께 보존한다. 이후 수정은 새 run-id에서 수행한다.

| 산출물 | 경로 |
|---|---|
| XPR | `D:/2610_MFCC/build/system_fixed/s06/design/vivado_project/zybo_z7_20_fixed.xpr` |
| Bitstream | `D:/2610_MFCC/build/system_fixed/s06/design/zybo_z7_20_fixed.bit` |
| XSA | `D:/2610_MFCC/build/system_fixed/s06/design/zybo_z7_20_fixed.xsa` |
| 재생성 Tcl | `D:/2610_MFCC/build/system_fixed/s06/design/zybo_z7_20_fixed_recreate.tcl` |
| 생성·구현 Tcl snapshot | `D:/2610_MFCC/build/system_fixed/s06/source/hardware/system/create_fixed_system.tcl` |
| Synth/route DCP | `D:/2610_MFCC/build/system_fixed/s06/design/synthesized.dcp`, `routed.dcp` |
| ARM ELF | `D:/2610_MFCC/build/arm_accel/a06/binaries/mfcc_accel_demo.elf` |
| PS 초기화 Tcl | `D:/2610_MFCC/build/arm_accel/a06/ps7_init.tcl` |

| 파일 | SHA-256 |
|---|---|
| XPR | `68009c536b170316dbf2816fe6183adbe3989cd2113392ce1eb7f3de07bef2d8` |
| Bitstream | `d364574bce6e191e898d8971061aac391aa9a858b2aab063b849cf7193b66f68` |
| XSA | `2ca6f295ddf7547b890368967457c1533f09122239a886dbe1b5ada0f2b156dd` |
| ELF | `58ed18398fbdbc2fceb0d07ce2caefc473bc4845f6336bcd1a186f2ad2eb0e3a` |
| PS 초기화 Tcl | `126a7277430f44f331d35eafcfa15fc74826a8dcf979ef20f0c30781ca002124` |
| 시스템 run_manifest.json | `406527cb18ea344fa0d63bd391b2ca1044229b51a69092ac99d89579936ae604` |
| 시스템 artifact_hashes.json | `beffafa49e2c2e0e5e02109016c5a23e2666e4b866a3c62ec4404e040d923cf9` |
| ARM build_manifest.json | `8882378e327beb708074310a0fced43ee724f56282b4660130838876789203c8` |
| ARM artifact_hashes.json | `2cffe47fde88ef23d501d2d06bf5342d4bea0bbff1ab62c8e413940b040d4b0b` |

## 재현 명령

PowerShell 작업 위치는 `D:/2610_MFCC/project`다. 아래 ID는 예시이며 실행할 때마다
존재하지 않는 이름을 고른다. Windows의 vendor checkpoint 경로 제한 때문에
`bd/all`은 실행 루트 40자 이하, 이 workspace에서는 **run-id 8자 이하**를 사용한다.
다른 Vivado/XSim 작업이 끝난 뒤 직렬 실행한다. runner의 프로세스 guard를 우회하지 않는다.

```powershell
$py = 'D:/2610_MFCC/build/python_reference/venv/Scripts/python.exe'
& $py -B scripts/run_fixed_system.py --run-id smkNEW --smoke --mode sim
& $py -B scripts/run_fixed_system.py --run-id sysNEW --mode all
$xsa = 'D:/2610_MFCC/build/system_fixed/sysNEW/design/zybo_z7_20_fixed.xsa'
$digest = (Get-FileHash -LiteralPath $xsa -Algorithm SHA256).Hash.ToLowerInvariant()
& $py -B scripts/build_arm_accel.py --run-id armNEW --host-tests --xsa $xsa --xsa-sha256 $digest
& $py -B verification/system/audit_fixed_system.py --run D:/2610_MFCC/build/system_fixed/sysNEW --out D:/2610_MFCC/build/system_fixed/sysNEW_audit.json
& $py -B verification/system/audit_ps_configuration.py --run D:/2610_MFCC/build/system_fixed/sysNEW --out D:/2610_MFCC/build/system_fixed/sysNEW_ps_audit.json
```

`--mode all`은 전체 AXI/단위 simulation, BD/IP 생성, 합성·구현·bitstream/XSA,
생성 PS7 버스 모델 simulation까지 포함한다. 실제 `s06` 실행은 아래와 같이 최종
RTL의 성공한 시뮬레이션을 인증해 재사용했다. 이 명령의 `s06`과 `a06`은 이미
존재하므로 다시 실행할 때는 새 짧은 ID를 사용해야 한다.

```powershell
& $py -B scripts/run_fixed_system.py --run-id s06 --mode all --verified-simulation D:/2610_MFCC/build/system_fixed/system_full_20261005_05
& $py -B scripts/build_arm_accel.py --run-id a06 --host-tests --xsa D:/2610_MFCC/build/system_fixed/s06/design/zybo_z7_20_fixed.xsa --xsa-sha256 2ca6f295ddf7547b890368967457c1533f09122239a886dbe1b5ada0f2b156dd
```

실행한 명령·도구 로그·소스 snapshot은 각 manifest와 실행 폴더에 있다.
Vivado의 Windows cscript worker를 허용하는 실행 권한이 필요했다. ARM 독립 감사의
추가 binary 검사 소스는 `D:/2610_MFCC/build/arm_accel/audit_a06.py`에 보존했다.

## 실패 실행과 수정 이력

아래 실행은 모두 `D:/2610_MFCC/build/system_fixed` 아래 보존했다. 수정은 신규
시스템 포장·전송·실행 도구에 한정했고 fixed 산술 계약은 바꾸지 않았다.

| 실행 | 결과 / 다음 수정 |
|---|---|
| `system_prepare_20261004_01` | Vivado 전 준비 snapshot, 성공한 구현으로 취급하지 않음 |
| `system_smoke_20261005_01` | Tcl `-testplusarg` 옵션 해석 실패 → `set_property -dict` 사용 |
| `system_smoke_20261005_02` | 단위 14개 + 실제 core 10프레임/130 MFCC PASS |
| `system_full_20261005_01` | 전체 616프레임 PASS; 직접 SV module-reference 실패 → custom IP 패키징 |
| `system_full_20261005_02`, `system_bd_20261005_01` | 패키저가 scalar `wire logic` 포트를 인식하지 못함 |
| `system_bd_20261005_02` | IP memory map 속성 오류 → `slave_memory_map_ref` 적용 |
| `system_bd_20261005_03` | 외부 port 수 검사 오류 → DDR/FIXED_IO 구성 포트를 기준으로 검사 |
| `system_bd_20261005_04` | 패키징·BD 생성과 PS 설정 감사 PASS; 구현은 미실행 |
| `system_full_20261005_03` | 해당 RTL 전체 simulation PASS; cscript worker 권한 오류로 구현 중단 |
| `system_full_20261005_04` | worker 권한 해결; default_nettype none 아래 input logic 합성 오류 → 배선 top 입력을 input wire로 명시 |
| `system_full_20261005_05` | 최종 RTL 전체 simulation PASS; accelerator 합성 PASS; vendor IP checkpoint 경로 261 bytes로 중단 |
| `s06` | 짧은 경로에서 최종 전체 시스템 구현·PS simulation PASS; 동일 최종 RTL simulation 인증 재사용 |

`full_03`의 멈춘 프로세스는 command line으로 해당 실행의 소유 PID를 확인한 뒤
종료했다. 다른 FP32 채팅의 프로세스는 종료하거나 메시지를 보내지 않았다.

## FP32 적용 차이와 남은 검증

기존 `fp32_mfcc`의 실제 포트를 읽어 차이를 정리했다. fixed의 start/done pulse와
sample-last 대신 FP32는 start-ready, 별도 end-ready, done-ready handshake를 쓴다.
FP32 출력은 binary32와 start_sample, error12이며 BFP는 없다. 같은 MMIO를 사용할 때
CORE_ID/FORMAT으로 구별하고 raw32를 출력 slot 하위 32비트에 담는다. start/end/done
adapter FSM, start_sample 검사와 오류 확장이 필요하다. 전송 규약에 이를 정의했지만
이번 작업에서 FP32 시스템 adapter RTL을 구현·검증한 것은 아니다.

남은 항목은 사용자 보류 범위인 실제 보드 revision/DDR/clock/reset 확인, bit/ELF
다운로드, ARM에서 실제 가속기 결과 회수·정수 비교, 저장 PCM 전체 clip 보드 실행,
전송/연산 성능 실측이다. post-route SDF 동적 시뮬레이션은 수행하지 않았으며
타이밍 결과는 전체 시스템 정적 타이밍 분석이다. BOOT.bin·flash 부팅도 범위 밖이다.

수치 모델의 개발 음성 RMSE 0.006260244715, 합성 최대 오차 6.591594298125와
floor 회귀 60건은 기존 상태로 보존한다. **비트 일치는 해당 정수 계약의 구현 성공이며
MFCC 수치 정확도 합격을 뜻하지 않는다.** 향후 수치 정책 수정은 별도 계약 버전과
검증이 필요하다.

FP32 완료 확인 후 사용자 지시에 따라 작업을 재개했다. 30분 간격 후속 자동화
`fp32-fixed-ps`는 목표 완료 후 2026-10-05에 **PAUSED**로 변경했고 앱의 성공 응답을
확인했다. 원래 작업 지시와 30분 주기는 보존했으며 추가 자동 실행은 중지했다.
