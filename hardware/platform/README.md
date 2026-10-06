# ZYBO Z7-20 PS-only ARM platform

2026-10-04에 Vivado 2024.2에서 생성·검증하고 XSA를 내보낸 최소 PS 플랫폼이다.
이 디렉터리의 스크립트는 물리 보드에 연결하거나 reset/program/download하지 않는다.
보드에서 DDR, UART 또는 ARM 프로그램이 동작한다는 주장은 별도 실행 기록으로만 판단한다.

## 재현

PowerShell에서 새 실행 이름을 지정한다. 기존 실행 디렉터리는 덮어쓰지 않는다.

```powershell
& D:\2610_MFCC\project\scripts\build_arm_platform.ps1 -RunId platform_04
```

- 실행 도구: `C:\Xilinx\Vivado\2024.2\bin\vivado.bat`
- 실제 성공 실행: `D:\2610_MFCC\build\arm_platform\platform_03`
- XSA: `platform_03\design\zybo_z7_20_ps.xsa`
- XSA SHA256: `d61cec0d7b3c5d1c6c741ef8f72f6761fb316ad9808c15a9cbbca36637c41bef`
- 실행 manifest: `platform_03\platform_manifest.json`
- 전체 PS 설정: `platform_03\reports\ps7_parameters.tsv`, `ps7_properties.txt`
- 로그: `platform_03\vivado.log`, `console.log`
- 생성 당시 스크립트·출처·라이선스 사본: `platform_03\provenance`

Vivado SW build는 5239630, IP build는 5239520이다.
`validate_bd_design`과 XSA export는 exit 0으로 완료했다.
`write_hw_platform -fixed -force -file ...`를 실행했고 `-include_bit`는 사용하지 않았다.
로컬 명령 도움말도 `reports\write_hw_platform_help.txt`에 저장했다.
합성·배치배선·bitstream 생성은 실행하지 않았다.

## 공식 보드 파일 고정

출처는 [Digilent vivado-boards](https://github.com/Digilent/vivado-boards)이며
commit `36f34ab687b7fa9c778b779d027f3bce63b3ace9`를 고정했다.
`board_source.json`은 파일별 불변 URL·SHA256을 기록한다. 스크립트는 필요한
`board.xml`, `part0_pins.xml`, `preset.xml`, `License.txt`만 받으며 전체 저장소를 복제하지 않는다.
캐시는 `build\arm_platform\officialsource\digilent-vivado-boards-<commit>`이다.
캐시 파일도 매번 SHA256을 확인하며 불일치 시 중단한다.

- 보드 정의: `digilentinc.com:zybo-z7-20:part0:1.2`
- 디바이스: `xc7z020clg400-1`
- 상류 경로: `new/board_files/zybo-z7-20/A.0`
- `board.xml`의 호환 revision: `B.2`; **실물 보드 revision은 아직 미확인**이다.
- `A.0` 폴더 이름을 실물 revision으로 해석하지 않는다.
- 저장소 라이선스는 MIT이며 원문을 보존했다. 보드 XML의 저작권 고지도 보존한다.

## 생성한 구성과 실제 설정값

IP Integrator의 `processing_system7:5.5` 하나에 공식 board automation preset을 적용했다.
외부 인터페이스는 `DDR`, `FIXED_IO` 두 개뿐이다. 모든 PS↔PL AXI 포트와
FCLK/FRESET 출력 포트를 끈 것 외에는 공식 DDR/MIO/PS 주변장치 설정을 유지했다.
AXI fabric, AXI DMA, MFCC IP, I2S, 오디오 codec, 마이크 입력은 추가하지 않았다.
공식 preset의 QSPI, SD0, GEM0, USB0, MIO GPIO는 남아 있지만 본 ARM 검증 경로에서 사용하지 않는다.

| 항목 | `platform_03` 설정/도구 산출값 |
|---|---|
| PS 입력 clock | 33.333333 MHz |
| CPU PLL / 실제 APU clock | 1333.333 MHz / 666.666687 MHz |
| DDR PLL | 1066.667 MHz |
| DDR 요청 / 실제 산출 clock | 533.333333 MHz / 533.333374 MHz |
| DDR 종류·폭 | DDR3 Low Voltage, 32 bit |
| preset DDR part | `MT41K256M16 RE-125` |
| DDR address 범위 | `0x00100000`–`0x3FFFFFFF` (보드 용량 1 GiB 중 낮은 예약 영역 제외) |
| UART | UART1, MIO48 TX / MIO49 RX, 115200 baud |
| UART clock / address | 100 MHz / `0xE0001000`–`0xE0001FFF` |
| MIO48/49 I/O | LVCMOS 1.8 V, pull-up enabled, slow slew |

표의 실제 clock은 Vivado 계산 속성이며 계측한 주파수가 아니다.
전체 timing/training, MIO, reset, enable 속성은 TSV에 보존했다.
네 DDR DQS-to-clock delay는 공식 preset 값 `-0.050`, `-0.044`, `-0.035`, `-0.100` ns다.
Vivado가 이에 대해 `PSU-1`–`PSU-4` critical warning을 출력했다. 값을 임의로 고치지 않았다.
AXI 포트를 끈 설계에는 BD 5-700/5-699의 빈 주소공간 경고도 있었다.
board image를 내려받지 않아 XSA export에 Project 1-645 경고가 있었다.
이 경고들과 별개로 BD validation 및 export는 완료했으며, DDR 실기 검증은 별도다.

## ARM 실행에 전달할 파일

XSA ZIP에는 `ps7_init.tcl`, `ps7_init.c/.h`, `ps7_init_gpl.c/.h`,
`ps7_init.html`, HWH, BDA, XSA/HWDEF metadata가 있으며 `.bit` 항목은 없다.
Vivado 생성 디렉터리의 초기화 파일은 다음과 같다.

```text
D:\2610_MFCC\build\arm_platform\platform_03\design\vivado_project\zybo_z7_20_ps.gen\sources_1\bd\zybo_z7_20_ps\ip\zybo_z7_20_ps_processing_system7_0_0\ps7_init.tcl
```

ARM 실행 도구는 같은 XSA의 초기화 파일과 BSP를 사용해야 한다.
PS 초기화는 별도 보드 실행 담당자가 대상 케이블·CPU를 확인한 뒤 수행한다.
`C:\Xilinx\Vitis\2024.2\bin\xsct.bat`의 설치 버전은 2024.2.0이며,
로컬 명령 설명은 `C:\Xilinx\Vitis\2024.2\scripts\xsct\xsdb\xsdb.tcl`에 있다.
`targets -target-properties`는 Tcl dict 목록을 반환하고, `targets -set -filter`는
일치 대상이 정확히 하나일 때만 선택한다. 메모리 byte 수를 지정할 때는
`mrd -size b -bin -file <file> <address> <byte_count>`를 사용한다.

## 물리 연결에 대한 공식 문서 근거

[Digilent Zybo Z7 reference manual](https://digilent.com/reference/_media/reference/programmable-logic/zybo-z7/zybo-z7_rm.pdf)
(2018-02-21, rev. B 대상)의 pp. 9–10, 12–14, 17을 확인했다. 실물 revision과 실크 인쇄를 먼저 대조한다.

- USB JTAG/UART는 **J12**의 FT2232HQ 경로다. USB Host/OTG 커넥터와 구분한다.
- JP6는 전원 선택이다. J12 USB 전원에는 USB, J17 외부 전원에는 WALL 표기를 따른다.
  외부 전원은 중심 양극 2.1 mm, 4.5–5.5 V이며 문서 권장은 2.5 A 이상이다.
- SW4가 전원 스위치이며 LD13 PGOOD가 전원 정상 상태를 표시한다.
- JP5가 부트 모드를 선택한다. JTAG 개발에는 보드의 JTAG 표기를 확인한다.
  본 문서는 사진 확인 없이 점퍼의 좌우/핀 번호를 단정하지 않는다.
- 기본 cascaded JTAG에서 PS를 접근한다. JP3를 short한 independent JTAG 모드에서는
  온보드 JTAG로 PS에 접근할 수 없다고 명시되어 있다.
- UART는 기본 115200, 8 data bits, no parity, 1 stop bit이다.

## 실패 기록

`platform_01`은 Tcl에 존재하지 않는 `redirect` 사용 때문에 시작 단계에서 중단했다.
`platform_02`는 정상 preset 적용/BD validation 후 `/DDR`와 `DDR` 이름 비교 오류로 중단했다.
둘 다 스크립트 문제이며 로그를 보존했다. 수정 후 `platform_03`을 새 디렉터리에서 생성했다.
실패 실행을 성공 실행으로 덮어쓰거나 실물 DDR/ARM 성공으로 재해석하지 않는다.
