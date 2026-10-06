# ARM 고정소수점 MFCC 가속기 전송

`mfcc_accel.c`는 새 PS/PL 시스템의 AXI4-Lite 폴링 드라이버다. 수치 연산은
PL의 기존 정수 MFCC가 수행한다. 기존 `software/arm`, `arm_fixed`, `c_fixed`
및 PS-only 플랫폼을 변경하거나 최종 BSP로 재사용하지 않는다.

기준 ABI는 `hardware/system/REGISTER_CONTRACT.md`이며 주소는
`0x43C00000`, little-endian 32비트, 모든 쓰기는 full-word이다. 계약은
`D:/2610_MFCC/build/fixed_contract/v2_pcm16_mfcc40_20261004_r2`,
`contract.json` SHA-256은
`283fff8abec22b3455a0219e37d27ac9c090ed2e74be3f7256677fb0cf4d6e3e`이다.
전송 드라이버의 통과와 수치 정확도 합격, 실제 보드 실행은 별개다.

## 전송 순서와 한계

`mfcc_accel_run()`은 ID/ABI/형식/계약 태그 등 9개 값을 확인한 뒤 ABORT,
SAMPLE_COUNT, START를 순서대로 쓴다. 각 폴링 회차에서 출력 한 개를 먼저
읽고 POP한 뒤 입력을 최대 한 개 쓴다. 한 개씩만 있는 입력·출력 슬롯의
backpressure 때문에 긴 PCM 전체를 먼저 쓰고 출력을 읽는 방법은 허용되지
않는다. 한 실행 소유자만 MMIO에 접근해야 한다.

입력 상한은 262144 samples이다. 출력 프레임 수는 T<512이면 0, 그 외에는
`1+(T-512)/160`이며 프레임마다 13 records이다. 최대 1636 frames/21268
records를 저장한다. 빈 입력과 512보다 짧은 입력도 START/DONE 경로를 따른다.
불완전한 마지막 프레임은 원래 수치 계약대로 폐기한다.

출력 record는 24 bytes이다. offset 0의 `int64_t value_q24`는 signed40/F24를
확장한 값, offset 8/12는 frame/index, offset 16은 signed BFP, offset 20은
last/error flags이다. BFP로 최종 MFCC 값을 다시 스케일하지 않는다. 드라이버는
전체 signed40 부호 확장, frame/index/last 순서, 프레임 내 BFP 일치와 범위,
reserved bits, error flag를 검사한다. 음수 디코딩은 unsigned64 비트 조립과
표현 가능한 magnitude 변환을 사용하므로 음수 shift에 의존하지 않는다.

DONE 때 마지막 출력이 남아 있을 수 있으므로 POP까지 완료하고 입력 written/
consumed, 출력 captured/popped 및 소프트웨어 개수를 모두 확인한다. 오류 때는
진단 값을 보존한 후 ABORT를 시도한다. 실패한 출력 배열은 부분 결과이며
성공 결과로 사용하면 안 된다. 잘못된 인자나 주변장치 ID에는 쓰기를 하지 않는다.

`timeout_ticks`와 `max_polls`가 모두 필수다. 후자는 timer가 정지해도 응답하는
폴링 루프를 끝낸다. **MMIO 접근 자체가 반환하지 않는 AXI 정지는 이 타임아웃으로
복구할 수 없다.** Xilinx 어댑터의 Xil_In32/Xil_Out32에 대한 SLVERR는 ARM data
abort를 일으킬 수 있다. 이 어댑터는 data-abort handler를 설치하지 않으며 그
예외를 C 반환값으로 바꾸지 않는다. 호스트 callback 오류 시험은 소프트웨어
인터페이스 검사로서 물리 bus-error 복구 검증이 아니다.

`mfcc_accel_xilinx.c`는 MMIO가 속한 1 MiB section을 Device/XN 속성으로 지정하고
DSB를 적용한다. 주소 범위에는 이 시스템의 가속기만 배치해야 한다. PCM과 출력은
일반 DDR에 있으며 DMA는 사용하지 않는다. busy-cycle 계수에는 ARM이 만든
backpressure가 포함된다. 보드 측정 전에는 성능 수치로 제시하지 않는다.

## 호스트 검사와 빌드

새 실행 폴더는 반드시 새 이름을 사용한다. 빌더는 원본, 생성 계수/벡터,
로그, manifest 및 SHA-256 index를 저장하고 기존 폴더를 덮어쓰지 않는다.

```powershell
& D:/2610_MFCC/build/python_reference/venv/Scripts/python.exe scripts/build_arm_accel.py --run-id transport_host_04 --host-tests
```

호스트 검사는 전송용 mock MMIO를 사용한다. 수치 RTL oracle이 아니다. 빈 입력,
1/511/512/672/1280/85920/262144 samples, 입력 gap/backpressure, 출력 잔류 DONE,
불완전 tail, 부호 양끝, timer wrap, cycle-high rollover, 오류 metadata, 잘못된
count, callback failure, timeout와 ABORT 후 재시작을 검사한다. 결과는 해당
실행의 `logs/host_tests.log` 및 `build_manifest.json`에 기록한다.

새 시스템 XSA가 준비되면 다음 옵션으로 **그 XSA에서 새 BSP**와 ELF를 만든다.
XSA의 실제 경로는 시스템 실행의 `design/zybo_z7_20_fixed.xsa`이다.

```powershell
& D:/2610_MFCC/build/python_reference/venv/Scripts/python.exe scripts/build_arm_accel.py --run-id system_xsa_build_01 --host-tests --xsa <새_시스템_XSA_절대경로> --xsa-sha256 <검증한_SHA256>
```

Vivado/Vitis 2024.2의 Cortex-A9 hard-float ABI와 새 standalone BSP를 사용한다.
ELF는 `binaries/mfcc_accel_demo.elf`, PS 초기화는 `ps7_init.tcl`, map/symbols/
ELF attributes와 컴파일 로그는 실행 폴더에 저장한다. 빌더는 XSA hash, 포함된
bitstream, 64 KiB 주소 영역, linker DDR 경계와 주요 DDR 심벌 크기를 검사한다.
새 BSP/ELF 성공 상태는 `BUILT_NOT_BOARD_RUN`이며 보드 접속·다운로드·실행,
BOOT.bin 생성, flash 쓰기는 수행하지 않는다.

## 데모와 향후 DDR 캡처

기본 데모는 고정 v2 r2의 첫 개발 음성에서 PCM 512개 및 정답 MFCC 13개를
생성해 ELF에 포함한다. 빌더는 먼저 발행 계약의 전체 artifact hashes를 검사한다.
기본 mode 0은 전송 검사 후 13개 정수와 BFP를 직접 비교한다. 정답을 실시간
float 연산으로 생성하지 않는다. mode 1은 외부 PCM 전송 검사이며 수치 비교는
캡처된 결과를 같은 입력의 Python 정수 정답과 별도로 해야 한다.

DDR 심벌은 ELF의 `arm_accel_layout`(16 x uint32)와 build manifest의 addresses로
확인한다. 고정된 linker 주소를 추측하지 않는다.

| 심벌 | 크기와 역할 |
|---|---|
|arm_accel_pcm|524288 bytes, little-endian signed16 input|
|arm_accel_results|510432 bytes, 21268 x 24-byte output record|
|arm_accel_control|32 bytes, 8 x uint32 설정|
|arm_accel_status|128 bytes, 상태 및 진단|
|arm_accel_layout|64 bytes, 실제 구조체 크기/offset/용량/주소/계약 태그|

control의 순서는 magic=0x4143434c, version=1, mode, sample_count,
timeout_seconds(1..60), max_polls(>0), pcm_crc32, reserved=0이다. CRC32는
little-endian PCM bytes의 표준 reflected CRC32(poly0xedb88320, init/final xor
0xffffffff)다. 빈 PCM의 CRC32는 0이다.

향후 승인된 보드 실행에서 `arm_accel_ready_breakpoint`에 중단점을 걸면 데모가
입력/제어 cache를 정리한 뒤 READY 상태로 멈춘다. 이 시점에 host가 PCM과
control을 DDR에 기록하고 재개한다. 재개 직후 CPU는 해당 cache lines를
invalidate한 뒤 검증한다. 결과는 cache flush 후 `arm_accel_result_breakpoint`
에서 캡처한다. 입력 교체는 반드시 ready 중단 시점에 수행해야 한다.

status 첫 8 words는 magic/version/state/result/numeric_checked/numeric_passed/
failure_index/pcm_crc32, 다음 uint64는 timer_hz, 그 뒤 offset40은 88-byte
`mfcc_accel_stats`이다. state는 READY1/RUNNING2/DONE3/ERROR4이며 result=0만
전송 성공이다. mode 0의 수치 성공은 numeric_checked=1과 numeric_passed=1까지
필요하다. 데모 오류 -100/-101/-102는 각각 control/CRC/수치 비교 실패다.
timer_hz는 BSP 설정값이며 실측 clock 검증 결과가 아니다.

## 검증된 새 시스템 빌드

2026-10-05의 `D:/2610_MFCC/build/arm_accel/a06`에서 새 PS/PL XSA를 사용한
BSP/ELF 생성이 완료됐다. 기존 BSP는 최종 링크에 사용하지 않았다.

- XSA: `D:/2610_MFCC/build/system_fixed/s06/design/zybo_z7_20_fixed.xsa`
- XSA SHA-256: `2ca6f295ddf7547b890368967457c1533f09122239a886dbe1b5ada0f2b156dd`
- ELF: `D:/2610_MFCC/build/arm_accel/a06/binaries/mfcc_accel_demo.elf`
- ELF SHA-256: `58ed18398fbdbc2fceb0d07ce2caefc473bc4845f6336bcd1a186f2ad2eb0e3a`
- 호스트 전송 검사: 968295 checks PASS.
- 독립 audit: `D:/2610_MFCC/build/arm_accel/a06_audit.json`, 2914 checks PASS,
  산출물 1424개 SHA 및 ELF 내부 PCM512/정답13/DDR descriptor 확인.

ELF는 223984 bytes, entry 0x00100000의 ARM ELF32 little-endian EABI5
hard-float/VFPv3이다. 모든 LOAD segment 끝은 0x0021c4d0 이하로 예약 DDR
0x00100000..0x020fffff 안에 있다. BSP에는 가속기 주소
0x43C00000..0x43C0FFFF와 UART1 주소 0xE0001000이 생성됐다. ELF header,
link map, disassembly, stack-usage 및 BSP/컴파일 로그는 a06에 보존돼 있다.
보드 연결·다운로드·실행·실측 성능과 ARM에서의 실제 가속기 수치 비교는 미실행이다.
