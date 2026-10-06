# Vivado 2024.2 FP32 IP contract

2026-10-04 KST. 최신 생성 실행은 `D:\2610_MFCC\build\fp32_hw\ip_04`이다.
`create_fp32_ips.tcl`은 IP catalog에서 새 IP 5개를 만든다. 원본 XCI/DCP를 수정하거나
구버전 netlist를 이식하지 않는다. 참고 설계의 arithmetic 역할만 비교했다.

```powershell
& D:\2610_MFCC\project\scripts\build_fp32_ips.ps1 -RunId ip_reproduce_01
```

새 실행 이름이 필요하다. 기존 실행과 실패 기록은 덮어쓰지 않는다.
Vivado 프로젝트는 `ip_04\project\fp32_ips.xpr`이며 target은 `xc7z020clg400-1`이다.
Vivado SW build 5239630, Floating Point **7.1 Rev.19**, FFT **9.1 Rev.13**을 직접 확인했다.
`generate_target all`로 simulation/synthesis HDL, instantiation template, C model,
vendor demo TB를 생성했다. 이 IP 생성 run은 합성·배치배선·bitstream·보드 실행을 하지 않는다.
전체 top 합성은 별도 `synth_*` 실행이며 해당 frozen RTL/IP에만 적용한다.
IP 설정의 100 MHz 자체는 timing 통과나 계측 결과가 아니다.

## 근거와 경로

- `ip_manifest.json`: 생성 명령 소스와 산출 HDL/XCI/XDC/설정 보고서 SHA256, exit code.
- `reports/*_properties.tsv`: Vivado가 실제 받아들인 전체 CONFIG 속성.
- `reports/ip_contract.json`: 실제 VEO 포트 및 XCI parameter, `ip_03` 대비 차이.
- `reports/ip_status.txt`: 5개 모두 Up-to-date, target Zynq, New License=Included.
- XCI: `project\fp32_ips.srcs\sources_1\ip\<name>\<name>.xci`.
- 생성물: `project\fp32_ips.gen\sources_1\ip\<name>`.
- 포트의 직접 근거: 각 생성물의 `<name>.veo`, simulation/synthesis wrapper.
- 최신 XCI는 JSON instance 형식이다. 확장자만 보고 이전 XML parser를 적용하지 않는다.

`Included`는 해당 catalog 보고 값이다. 생성은 성공했지만 전체 합성 라이선스 실행을
대신하지 않는다. Vendor HDL의 AMD 저작권·라이선스 고지를 보존한다.
`*_bmstub.v`는 black-box stub이며 실제 simulation model의 대체물이 아니다.
통합 Vivado 프로젝트에서 XCI를 읽고 vendor simulation 의존성을 사용한다.

## 공통 FP operator 인터페이스

모든 operator는 `aclk`, `aclken`, 동기 active-low `aresetn`, Blocking flow control,
`C_Rate=1`, `Maximum_Latency=true`, `m_axis_result_tready`를 사용한다.
TLAST/TUSER/exception 출력은 생성하지 않았다. 유한성 검사는 wrapper/검증기 책임이다.

| 모듈 | 입력 | 출력 | `C_Latency` |
|---|---|---|---:|
| `fp32_mul` | A32, B32 | result32 | 9 |
| `fp32_addsub` | A32, B32, operation8 | result32 | 12 |
| `fp32_log` | A32 | result32 | 23 |
| `fp32_pcm16` | A16 signed W16/F15 | result32 | 7 |

각 입력은 `s_axis_<a/b/operation>_tdata/tvalid/tready`이며 출력은
`m_axis_result_tdata/tvalid/tready`다. A/B/operation은 독립적으로 수락된다.
wrapper는 각 operand를 한 번만 전송하고, 대응 결과를 소비할 때까지 요청을 보존한다.
Blocking 모드에서 stall/buffering을 포함한 외부 지연은 표의 pipeline latency와 같다고
가정하지 않는다. 결과 수락은 `valid && ready`로 확인한다.

`ip_04`는 보존된 `ip_03`에 비해 FP의 `Has_ACLKEN`, FFT의 `aclken`만 true로 바꿨다.
전체 component/model parameter 비교에서 `C_HAS_ACLKEN=1` 외 산술·latency 차이는 없고,
다섯 모듈 모두 1-bit input `aclken`만 추가됐다. CE=0일 때 IP는 상태와 출력을 유지한다.
따라서 wrapper는 CE=0인 사이클을 handshake로 세면 안 된다. 처리 중인 연산과
결과 수락까지 CE를 켜고, reset 해제 후 첫 복구 clock도 켜도록 설계한다.
reset은 CE보다 우선하며 최소 두 clock이 필요하다.
[PG060 v7.1 printed p.10](https://docs.amd.com/api/khub/documents/ym1A7qsltTGP_saZFTrikQ/content),
[PG109 ACLKEN](https://docs.amd.com/r/en-US/pg109-xfft/aclken-Clock-Enable),
[PG109 reset](https://docs.amd.com/r/en-US/pg109-xfft/aresetn-Synchronous-Clear)를 근거로 한다.
이는 vendor의 실제 clock-enable 입력 사용이며 clock net의 조합논리 gating이 아니다.
CE 추가 후 unit/integration simulation과 synthesis를 별도로 다시 확인한다.

`fp32_addsub`: operation `8'h00`은 A+B, `8'h01`은 A−B다.
직접 근거는 생성 `demo_tb/tb_fp32_addsub.vhd`의 `op_to_opcode`다.
`fp32_log`는 자연로그다. MFCC의 `max(E,1e-12)`는 IP 앞의 별도 RTL에서 적용한다.

[AMD PG060](https://docs.amd.com/api/khub/documents/ym1A7qsltTGP_saZFTrikQ/content)
pp. 5–6, 9–10, 16, 20은 RN-even, 대부분 연산의 subnormal→signed-zero 처리,
reset 최소 2 clock, signed fixed 표현을 설명한다. PC와 모든 특수값 동작이
같다고 주장하지 않는다. reset 해제 후 ready를 확인하며 첫 operand를 전송한다.

## PCM16 정규화

실제 최종 model parameter는 `C_A_WIDTH=16`, `C_A_FRACTION_WIDTH=15`이고
실제 포트도 `[15:0]`이다. GUI/Tcl의 `C_A_Exponent_Width`는 이 변환에서 **정수부 폭**이다.
따라서 설정은 `C_A_Exponent_Width=1`, `C_A_Fraction_Width=15`이다.
부호를 포함한 1+15=16 bit이며 수학적 값은 `signed_PCM * 2^-15`다.
이 단계에 별도의 `/32768` 곱셈을 다시 적용하지 않는다.

| 입력 PCM bit pattern | 기대 binary32 | 수학적 값 |
|---|---|---|
| `0000` | `00000000` | 0 |
| `0001` | `38000000` | 1/32768 |
| `FFFF` | `B8000000` | −1/32768 |
| `4000` | `3F000000` | 0.5 |
| `7FFF` | `3F7FFE00` | 32767/32768 |
| `8000` | `BF800000` | −1 |

위 표는 exact 표현으로 계산한 unit-test 기대값이다. 생성 성공을 해당 simulation
통과로 간주하지 않으며 실행 증거는 상위 FP32 검증 결과에 기록한다.

## FFT512 인터페이스

`fp32_fft512`는 N512, single channel, radix-2 burst I/O, non-realtime,
floating-point input/output, twiddle width25, natural input/output이다.
data/phase/reorder memory는 block RAM, complex multiplier는 resource DSP 선택이다.

| 포트 | 폭/역할 |
|---|---|
| `aclk`, `aresetn` | 단일 clock / active-low reset |
| `s_axis_config_tdata/tvalid/tready` | 24-bit 설정 AXIS |
| `s_axis_data_tdata/tvalid/tready/tlast` | 64-bit 입력, 샘플511에서 TLAST |
| `m_axis_data_tdata/tvalid/tready/tlast` | 64-bit 출력, 512개 complex bin |
| `m_axis_data_tuser` | 16-bit, `[8:0]` XK_INDEX |
| `event_frame_started` | frame 시작 알림 |
| `event_tlast_unexpected`, `event_tlast_missing` | framing 오류 |
| `event_status_channel_halt` | 생성된 event 출력; status AXIS 포트는 없음 |
| `event_data_in_channel_halt`, `event_data_out_channel_halt` | channel stall 알림 |

입출력 packing은 real `[31:0]`, imaginary `[63:32]`다. 설정 bit0은 forward=1,
inverse=0이고 `[18:1]`은 SCALE_SCH, `[23:19]`는 0이다. 생성 demo TB가 이 배치를
직접 명시한다. 통합 후보 설정은 **`24'h000001`**(forward, zero schedule)이다.
설정 수락 후 정확히512번 data handshake하고 모든512 출력과 index/TLAST를 검사한다.
non-realtime mode의 입출력 stall은 지원되므로 halt event 하나만으로 수치 실패를
단정하지 않는다. TLAST 누락/초과는 별도 오류다.

## FFT 수치 경계와 아직 검증할 점

[AMD PG109 Floating-Point Considerations](https://docs.amd.com/r/en-US/pg109-xfft/Floating-Point-Considerations)는
이 floating interface의 내부 fixed-point FFT/입력 normalization을 설명한다.
7-series의 이 IP를 모든 butterfly가 IEEE binary32인 FFT라고 쓰지 않는다.
새로운 DSPFP32 기반 native floating 구현과도 구분한다.

실제 `C_USE_FLT_PT=1`, `C_HAS_SCALING=1`, `C_HAS_ROUNDING=0`이다.
Vivado는 floating mode에서 `scaling_options=unscaled`, `rounding_modes=convergent_rounding`
요청을 비활성 속성 변경으로 무시했다. 최종 recipe는 그 요청을 제거했고 실제
표시값 `scaled`/`truncation`을 보존했다. 이를 근거로 FFT 이득을 추정하지 않는다.
`24'h000001`에서 impulse/DC/complex tone을 실행하여 출력 이득, 부호, 자연 순서,
512-bin 개수, gap/stall을 확인해야 한다. 데이터에 따른 이득 오차를 상수로 숨기거나
공통 규격·허용치를 바꾸지 않는다. MFCC power의 `/512`는 별도 공통 연산이다.

## 시도 이력

- `ip_probe_01`: 설치 catalog와 기본 속성 질의 성공.
- `ip_01`: fixed 변환에 Single 입력 precision을 지정하여 configuration 실패. 보존.
- `ip_02`: 생성 성공했으나 PCM 정수부16+소수부15가 W31/AXIS32임을 직접 확인.
  이 converter는 통합하지 않는다. FFT의 비활성 속성 무시 경고도 기록됐다.
- `ip_03`: PCM W16/F15 수정 및 비활성 요청 제거, 5개 IP 생성 exit0.
  속성 조회의 deprecated-property warning 2개 외 generation 오류 없음.
- `ip_04`: 다섯 IP에 native ACLKEN만 추가, 생성 exit0. 모든 산술 설정과 latency가
  `ip_03`과 동일함을 XCI 전체 parameter 비교로 확인. CE unit 결과는 별도 검증 run 근거다.

지금 확인한 범위는 fresh IP 생성과 포트/설정 계약이다. 수치 unit/integration simulation,
전체 MFCC 합성·timing·실보드 성능은 각각 별도의 실행 증거가 필요하다.
전체 top의 OOC 합성·배선 결과와 적용 범위는 [SYNTHESIS_FLOW.md](SYNTHESIS_FLOW.md)에 기록한다.
후속 compact Mel ROM/backend 구현도 동일한 `ip_04`를 사용하며 vendor IP 산술 설정을
변경하지 않았다. 계수 보관 형식·주소 제어와 실제 자원 결과는 해당 합성 문서에서 구분한다.
