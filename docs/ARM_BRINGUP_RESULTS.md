# ZYBO Z7-20 PS 전용 플랫폼·ARM 실행 준비 결과

작성: 2026-10-04 KST. 대상은 `comparison_raw13`, Cortex-A9 core 0 / standalone이다.

## 1. 직접 확인한 완료 상태

**ZYBO Z7-20 전용 XSA, standalone BSP, Hello World와 MFCC ARM ELF를 실제 생성·빌드했다. 현재 JTAG 대상과 보드 USB UART가 인식되지 않아 실제 ARM 실행·수치 비교·시간 측정은 하지 못했다.**

| 항목 | 상태와 근거 |
|---|---|
| 플랫폼/XSA 생성 | 완료. Vivado 2024.2 `validate_bd_design`, XSA export exit 0. PS7 한 개, DDR/FIXED_IO만 외부 연결 |
| ARM용 빌드 | 완료. Vitis 2024.2 standalone BSP 및 기본 Hello World, `hello_ddr.elf`, `mfcc_arm.elf` 링크 성공 |
| Hello World 실제 실행 | **미검증**. UART 수신 기록 없음 |
| DDR 검사 | 전용 4 KiB 영역·검사 코드·메모리 배치 확인. **보드 읽기/쓰기 미검증** |
| MFCC 실제 실행·수치 비교 | ELF·입력 계획·회수/대조 도구 준비. **실제 ARM 출력 없음** |
| 처리 시간 측정 | 타이머/반복/집계 코드 준비. **실제 측정값 없음** |

PC C의 개발 음성 534프레임 및 평가 음성 20개/9,501프레임 통과는 기존 [C 결과](C_REFERENCE_RESULTS.md)다. 합성 입력 `fullscale_alternating`, `tone_bin32_1000hz`의 수치 실패를 유지한다. 이번 빌드 성공을 전체 수치 검사를 통과한 골든 모델이나 ARM 검증 완료로 표현하지 않는다.

## 2. 실행 폴더와 재현

생성물은 모두 `D:/2610_MFCC/build/arm_platform/` 아래에 있다. 기존 실행 이름은 덮어쓰지 않는다.

| 폴더 | 실제 내용 |
|---|---|
| `platform_03` | 최초 성공 PS-only XSA, Vivado 로그, PS 속성 전체, 공식 보드 파일 출처/hash |
| `apps_04` | 최초 성공 BSP/ARM 어댑터 빌드, ELF/map/readelf/역어셈블리, 코어·계수 snapshot/hash |
| `reproduce_01_platform`, `reproduce_01_apps`, `reproduce_01_prepare` | **한 명령 전체 재생성 성공 실행**. 다음 보드 실행에 사용할 대표 산출물 |
| `prepare_02` | 최초 성공 오프라인 실행 준비, ABI/오류 처리 검사 40/40 |
| `connection_check_01` | 작성한 연결 조회 스크립트의 실제 Windows 장치 목록·XSDB target 목록 |
| `probe` | 초기 도구 도움말·연결 조회·BSP 탐색 로그 |
| `final_audit.py`, `final_audit.json` | 최종 artifact integrity·ELF 내부 계수·재빌드 load segment 비교 |

한 명령으로 **새 플랫폼 → BSP → ARM ELF → 기준 자료 integrity 검사와 오프라인 실행 준비**를 수행한다. 이 명령은 보드에 다운로드하지 않는다.

```powershell
& 'D:\2610_MFCC\project\scripts\build_arm_environment.ps1'
```

실제 재현 명령은 `build_arm_environment.ps1 -RunId reproduce_01`이며 exit 0이었다. 재실행할 때 이 이름은 이미 존재하므로 새 이름을 쓰거나 생략한다. 최종 manifest 상태는 앱 `built_not_board_verified`, 준비 `prepared_unverified_on_arm`이다. 개발 gate·평가 실행·시간 측정은 모두 false다.

최종 ELF는 `reproduce_01_apps/binaries/hello_ddr.elf`, `mfcc_arm.elf`다. 각각 SHA-256은 `eda3d6b335cbce0e8b6a69251b4f376257a9c812e91ce3d5c809aae31b3b17a0`, `dc3430018c58142f6a1489a78ff078a5af824a56381e96159835ad703565aa81`이다. 같은 실행의 XSA hash는 `8a721765b6b460fb899a98014105836b92fd76f8081e588dc8637ee078ecc1e6`이며, `build_manifest.json`이 해당 XSA/BSP/초기화 파일을 정확히 묶는다. 최초 XSA와 컨테이너 hash가 다르므로 서로 바꿔 쓰지 않는다.

오프라인 검사 40/40은 CRC·프레임 경계·ABI/byte 수·NaN·순서·trace 범위·대상 선택·클록 변경·정지 타이머·새 실패 차단 등을 확인했다. Python 기준 artifact 1,321개와 PC C artifact 732개, 입력 38개를 검증했다. 개발 534·평가 9,501프레임은 입력/기준값의 개수 확인이며 ARM 출력 측정이 아니다.

최종 감사에서 최초/재생성 앱의 artifact **각 1,434개**, 준비 실행의 **각 8개** hash가 일치했다. 두 MFCC ELF에서 실제 연결된 계수 **8,059개 float32의 바이트**가 기존 C 계수 binary와 전부 같았다. 최초/재빌드 ELF의 load segment 주소·크기·내용도 동일했다. 경로와 debug 정보가 들어간 ELF 전체 파일의 byte 동일성이나 보드 수치 재현을 주장하지 않는다. IDE의 active lock/metadata/log는 artifact index에서 명시적으로 제외했다.

개별 ARM 빌드는 다음과 같다. `--run-id`를 생략하면 새 시각 이름을 만든다.

```powershell
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B `
  'D:\2610_MFCC\project\scripts\build_arm_apps.py' `
  --platform-run 'D:\2610_MFCC\build\arm_platform\platform_03'

& 'D:\2610_MFCC\project\scripts\probe_arm_connections.ps1' -StartServer
```

플랫폼 생성은 [hardware/platform](../hardware/platform/README.md), 어댑터는 [software/arm](../software/arm/README.md), 실행/검증은 `scripts/run_arm_reference.py` 및 `verification/arm/`에 있다. `software/c/` 계산 코어와 과거 결과 폴더는 수정하지 않았다. 생성된 PCM·ELF·BSP·그림/자료는 Git 밖에 유지하며 commit/push하지 않았다.

## 3. 보드 설정과 출처

공식 [Digilent vivado-boards](https://github.com/Digilent/vivado-boards/tree/36f34ab687b7fa9c778b779d027f3bce63b3ace9/new/board_files/zybo-z7-20/A.0)의 commit `36f34ab687b7fa9c778b779d027f3bce63b3ace9`를 고정했다. 필요한 보드 XML·핀·preset·MIT 라이선스만 확보했다. 다른 Zynq 보드 preset을 사용하지 않았다.

| 식별 항목 | 값 |
|---|---|
| board part / device | `digilentinc.com:zybo-z7-20:part0:1.2` / `xc7z020clg400-1` |
| 상류 폴더 / 호환 revision | `A.0` / board.xml의 `B.2`; **실물 revision 미확인** |
| board.xml SHA-256 | `650c4ca19979c156f059eee2b2f9a1862cbc1eb25927f2ab1d1904968ab342cc` |
| preset.xml SHA-256 | `f1da413772d4be2d6c2a703ceb54502304840e11fe8fd8c2b02dc450c86335e6` |
| platform_03 XSA SHA-256 | `d61cec0d7b3c5d1c6c741ef8f72f6761fb316ad9808c15a9cbbca36637c41bef` |
| 같은 XSA의 ps7_init.tcl SHA-256 | `49af037f2b6bbe883c2499969da844119dc1cb85a26f404be7ca85d87c54284e` |

모든 파일의 URL·hash는 `hardware/platform/board_source.json` 및 실행의 `platform_manifest.json`에 있다. `reports/ps7_parameters.tsv`에 전체 PS 설정을 보존했다.

- PS 입력 33.333333 MHz; Vivado 산출 CPU 666.666687 MHz, DDR 533.333374 MHz.
- DDR3L 32 bit, preset part `MT41K256M16 RE-125`; 소프트웨어 DDR 범위 `0x00100000`–`0x3FFFFFFF`.
- UART1: MIO48 TX / MIO49 RX, 1.8 V, 115200 baud, 주소 `0xE0001000`, UART clock 100 MHz.
- 공식 preset의 DDR/MIO·PS 주변장치 설정을 유지하고 PS↔PL AXI/FCLK/FRESET 포트만 해제했다. QSPI·SD0·GEM0·USB0·GPIO 설정은 남지만 본 프로그램은 사용하지 않는다.
- MFCC PL IP·AXI DMA·마이크/I2S/코덱을 추가하지 않았다. 합성·구현·bitstream 생성·Flash 기록·SD boot image 제작을 수행하지 않았다.

**위 주파수는 도구가 산출한 설정값이다. 보드 레지스터 확인이나 외부 계측 결과가 아니다.** 공식 DQS-to-clock 값 `-0.050/-0.044/-0.035/-0.100 ns`에 Vivado가 PSU-1…4 critical warning을 냈다. 값을 임의 수정하지 않았으며, BD validation/export 성공은 실제 DDR 정상 동작 증거가 아니다. AXI 포트가 없는 데 따른 빈 주소공간 경고 및 생략한 board image 경고도 로그에 남겼다.

## 4. ARM 도구·수치 연산·메모리

설치 도구는 Vivado SW build 5239630 / IP build 5239520, XSCT 2024.2.0 SW build 5239620이다. XSCT는 이 버전에서 deprecated 경고가 있지만 실제 platform/domain/BSP/template 생성과 빌드가 동작했다. `software/arm/create_bsp.tcl`에 UART1 stdin/stdout와 BSP 설정을 기록했다. 부팅 BSP 생성을 끄고 `ps7_cortexa9_0` standalone domain만 만든다. 생성된 `system.mss`에서 standalone **9.2**, cpu_cortexa9 driver **2.12**를 확인했다.

컴파일러는 Vitis 동봉 ARM GCC **13.3.0**, 링크된 Newlib는 **4.4.0**이다. 애플리케이션 플래그:

```text
-mcpu=cortex-a9 -mfpu=vfpv3 -mfloat-abi=hard -std=c11 -O2 -g3
-fno-fast-math -ffp-contract=off -fexcess-precision=standard
-fno-tree-vectorize -Wall -Wextra -Werror -Wno-error=enum-compare
```

마지막 옵션은 기존 C의 서로 다른 enum 상수 간 크기 static assert에 대한 GCC 경고만 오류로 승격하지 않는다. 경고는 로그에 보존한다. 코어의 계산이나 검증 조건을 변경하지 않는다. BSP에도 Cortex-A9/VFPv3/hard-float 및 fast-math/FMA 정책을 명시했다. 생성된 `Xilinx.spec`를 절대 경로로 연결하여 AMD vector/startup을 사용한다. 기본 bare-metal `crt0`로 대체하지 않는다.

컴파일 매크로에서 `FLT_EVAL_METHOD=0`, hard-float ABI를 확인했다. ELF는 ARM ELF32 little-endian / EABI5 / VFPv3이며 시작점은 `0x00100000`이다. 코어 자체 단계·누산·저장은 기존 binary32다. **링크된 Newlib `logf` 내부는 역어셈블리에서 float→double 변환과 double 근사 연산이 확인됐다.** 입력/출력 형식은 float이지만 실행 파일의 모든 내부 연산이 binary32라고 주장하지 않는다. PC CRT와 동일 구현도 아니다. VFMA/VFMS 명령은 없으며 VFPv3 VMLA는 발견됐다. fused 명령과 구분한다([Arm 명령 설명](https://documentation-service.arm.com/static/5f3fa899428f7a6b3328fd44), §§4.46–4.47, 4.67).

펌웨어는 FPSCR rounding mode=nearest, FZ=0, DN=0을 설정하고 실행 전후를 기록한다. 이 설정 코드의 컴파일을 확인한 것이며 실제 레지스터 값은 보드에서 회수해야 한다. 계수는 기존 `c_reference/reproduce_01/coefficients/mfcc_tables.c/.h`와 byte 동일한 파일을 복사·컴파일한다. 계수를 다시 계산하거나 float64 바이너리를 재해석하지 않는다.

`apps_04`의 linker/map에서 직접 확인한 배치는 다음과 같다. 주소는 이 ELF의 값이며 다운로드 도구는 매번 ELF 심볼을 읽는다.

| 영역 | 배치/크기 |
|---|---|
| 전체 앱 허용 DDR | `0x00100000`부터 32 MiB, 초과 시 link 실패 |
| PCM | `0x00237A40`, 524,288 bytes; 최대 262,144 PCM16 samples |
| MFCC 결과 | `0x00219A40`, 122,880 bytes; 2,048 × 60-byte records |
| pre-emphasis trace | `0x00119A40`, 1,048,576 bytes |
| core state / frame | 정적 영역, 실제 ARM 크기 6,184 / 7,512 bytes |
| main stack | 64 KiB; 예외용 stack 포함 `.stack` 전체 71,680 bytes |
| DDR 검사 예약 영역 | `0x002CC000`–`0x002CCFFF`, 정확히 4,096 bytes, 별도 `.ddr_test` NOLOAD |

검사 영역은 코드·계수·입력·결과·heap·stack과 겹치지 않는다. linker ASSERT로 크기·상한을 제한했다. 큰 배열을 stack에 두거나 프레임마다 동적 할당하지 않는다. DDR 검사는 그 예약 영역만 0/1/교대비트/주소 기반 패턴으로 쓰고 cache clean/invalidate 후 읽는다. 전체 DDR 용량·모든 주소선의 정상 동작을 보증하는 시험은 아니다.

## 5. 입력·실행·회수·검증 경로

호스트는 고정된 Python/C 실행의 manifest/hash, PCM bytes, 계수, 소스, 허용치를 검사한다. 이후 순서는 다음과 같다.

1. 실제 케이블 serial과 CPU0 target을 하나로 선택하고 UART를 연다. 같은 XSA의 `ps7_init.tcl`임을 hash로 확인한 뒤 PS/DDR 초기화, Hello/UART marker와 예약 DDR 결과를 확인한다.
2. ELF를 다운로드하여 ready breakpoint까지 실행한다. 초기 `.bss`의 dirty cache가 다운로드한 PCM을 덮지 않도록 입력 전체를 미리 clean/invalidate한다.
3. 동일 PCM을 ELF의 `arm_pcm` 주소에 적재하고 **전체 PCM을 다시 읽어 SHA-256**을 확인한다. ARM도 CRC32를 계산한다. 불일치 시 MFCC를 실행하지 않는다.
4. 설정 64 bytes를 쓰고 RUN command를 마지막에 쓴다. 계산 완료 뒤에만 breakpoint에서 멈추어 결과·상태를 회수한다.
5. 결과는 LE `uint32 frame_id`, `uint32 start_sample`, `float C0…C12`의 60 bytes다. 프레임 수/순서/누락/크기/유한값은 수치 오차와 별도로 확인한다. 개발 음성은 정확히 534×13, 평가 20개는 합계 9,501×13이어야 한다.
6. ARM↔PC C와 ARM↔Python float64를 각각 비교한다. bit 동일성은 별도 진단이며 수치 수락의 가정이 아니다. 원래 `verification/c/tolerances.json`을 변경 없이 사용한다.

합성 입력은 모든 완전 프레임의 trace, 개발/평가 음성은 첫 프레임과 최초·최대 출력 차이 프레임의 trace를 회수한다. pre-emphasis는 클립 전체, 나머지는 요청 프레임의 window/complex FFT/power/Mel/log/DCT를 비교한다. trace ABI는 ARM이 내보낸 offset descriptor로 읽어 struct padding을 추측하지 않는다. 0/511샘플의 0프레임 입력에는 `UINT32_MAX` sentinel로 pre-emphasis만 회수한다.

기존 합성 실패는 사례 이름만 보고 허용하지 않는다. 기존과 동일한 실패 단계·원소 mask를 보이고 ARM↔PC 기준을 만족할 때만 제한을 유지한 평가 진행을 허용한다. 새 실패는 평가를 막는다. `controlled_evaluation_allowed`와 모든 수치 기준 통과는 다른 상태다. 평가에는 실패 예외 목록을 적용하지 않는다. 음성의 모든 중간 프레임을 dump한 것처럼 보고하지 않으며 실제 trace coverage를 기록한다.

## 6. 시간 측정 조건과 아직 없는 결과

기본 조건은 cache enabled, 워밍업 3회, 측정 30회, scalar 기준 구현이다. 개발 음성의 실제 수치 검증 후 측정하고 실행 조건·결과를 동결한 뒤 평가20으로 확대한다. 현재는 오프라인 조건만 준비했으며 실제 개발 gate를 통과한 상태가 아니다.

측정 구간은 `mfcc_init` + 전체 PCM 처리 + frame 검사 + 결과 checksum 소비다. PCM 다운로드, 입력 CRC, UART, 회수, cache 유지보수, 별도의 stage trace·결과 복사는 제외한다. 계산 코어가 원래 수행하는 단계 배열 저장은 포함한다. 측정 중 debugger로 멈추지 않으며 반복별 checksum을 검증 실행과 대조한다. 이것을 SIMD/NEON 등 최적화된 ARM 구현 대비 성능으로 표현하지 않는다.

XTime global timer를 사용한다. CPU 두 주기당 한 tick이라는 정의는 [AMD 2024.2 XTime 문서](https://docs.amd.com/r/2024.2-English/oslib_rm/Arm-Cortex-A9-Time-Functions)와 설치된 `xtime_l` 소스에서 확인했다. 이 BSP의 nominal timer 값은 333,333,343 Hz다. CPU/타이머 BSP 값, SLCR PLL/clock 설정, timer control, L1/L2 cache, CPSR, FPSCR, compiler/libm/hash를 회수·검사해야 시간을 해석할 수 있다. 도구는 같은 XSA의 초기화 mask와 회수한 clock register를 대조하고 CPU/2 및 prescaler=0, enable, 0이 아닌 tick을 요구한다. 이 확인도 외부 발진기의 실제 주파수 계측을 대신하지 않는다. 타이머 호출 오버헤드는 별도 기록하고 임의로 빼지 않는다. min/median/p95/max와 원시 tick을 보존한다. 입력 CRC가 먼저 입력을 읽으므로 cold-cache 시험이 아니다. 전체 clip 시간/프레임 수는 평균 처리 비용이며 개별 프레임 지연을 직접 측정한 값이 아니다.

**현재 min/median/p95/max, ARM 오차, 보드 DDR/UART 결과는 모두 미검증이다. PC 실행 시간을 환산해 채우지 않았다.**

## 7. 실제 보드 연결에 필요한 조작

Windows 장치 조회에서 일반 COM1 및 Bluetooth COM3/4/5/6만 확인됐다. USB UART/JTAG 관련 장치 매치가 없었다. XSDB 2024.2는 로컬 hw_server에 정상 연결했지만 `targets`, `jtag targets`, target properties가 모두 비어 있었다. 이것만으로 보드 고장이나 특정 전원/드라이버 원인을 단정하지 않는다.

[Digilent 공식 매뉴얼](https://digilent.com/reference/_media/reference/programmable-logic/zybo-z7/zybo-z7_rm.pdf)의 rev. B 설명을 확인했다. 실물 revision·실크 인쇄를 먼저 대조한 후 다음 상태가 필요하다.

- 데이터 통신 가능한 USB 케이블을 **J12 USB JTAG/UART**에 연결한다. USB Host/OTG 포트와 구분한다.
- USB 전원을 쓰면 **JP6=USB**, 외부 전원을 쓰면 **JP6=WALL**과 적합한 전원을 사용한다. **SW4 ON**, **LD13 PGOOD**를 확인한다.
- **JP5는 JTAG 표기 위치**, **JP3는 기본 cascaded JTAG(독립 모드 short 해제)**로 확인한다. 사진 없이 좌우 핀 위치를 단정하지 않는다.
- 연결 조회를 재실행하여 실제 케이블 serial, CPU0 target, 새 USB UART COM 번호를 확인한다. UART는 **115200, 8N1, flow control 없음**이다.

그 뒤 실행 명령에 관측한 `--target-filter`, `--cable-serial`, `--uart-port`, `--acknowledge-board ZYBO_Z7_20`을 지정한다. 대상 선택은 정확히 하나만 허용한다. Flash나 SD 부팅 변경은 필요하지 않다. 실제 보드가 인식될 때까지 실행 성공과 성능을 보고하지 않는다.

## 8. 보존한 실패·남은 항목

`platform_01/02`는 Tcl 명령/인터페이스 이름 검사 오류, `bsp_probe_01`은 BSP target flags 누락, `apps_01`은 enum 경고의 오류 승격, `apps_02/03`은 startup specs 연결/경로 문제였다. `apps_03`에서는 IDE lock 파일의 hash 시도도 실패해 후속 index에서 transient IDE metadata를 명시적으로 제외했다. `prepare_01`은 무관한 local symbol의 동명 출력을 export 충돌로 읽은 parser 오류였다. 새 폴더에서 수정·빌드했으며 기존 로그를 덮어쓰지 않았다. 이 실패들은 보드 수치 오류가 아니다.

남은 순서는 실물 revision·연결 확인 → PS 초기화와 Hello UART → 예약 DDR 검사 → 합성17/개발1 ARM 수치 비교 → 개발 측정·조건 동결 → 평가20과 측정이다. 합성 2개 기존 C 실패와 새 ARM 실패를 계속 구분한다. FFT 검토 문서·검증 폴더 및 기존 FFT/RTL 원본은 수정하지 않았다.
