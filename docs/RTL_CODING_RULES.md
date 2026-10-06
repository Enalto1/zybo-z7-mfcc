# MFCC RTL 코딩 규약

버전: 1.0 / 작성: 2026-10-04 KST.

사용자의 VGA 프로젝트 코딩 스타일을 이번 MFCC에 적용한다. **신규 사용자 작성 RTL의 필수 규칙**이다. 언어는 합성 가능한 SystemVerilog이며, 사용자가 대화에서 부르는 Verilog RTL을 이 문서에서는 `.sv`로 구체화한다. 대상은 ZYBO Z7-20 / `xc7z020clg400-1` / Vivado **2024.2**다.

공통 알고리즘은 [MFCC_SPEC.md](MFCC_SPEC.md), 양자화 설계 절차는 [FIXED_POINT_DEVELOPMENT.md](FIXED_POINT_DEVELOPMENT.md)를 따른다. 이 문서는 고정소수점 비트 폭이나 알고리즘 허용오차를 임의로 확정하지 않는다.

## 1. 출처와 적용 범위

스타일 출처는 `D:\202609_VGA_project\handoff\CODING_STYLE.md` 및 최신 simple판의 `SIMPLE_PHASE2_CONTROLLER_CORE.md` 10장이다. 구조 참조는 `C:\OndeviceAI\20260425_UART\20260425_UART.srcs\sources_1\imports\20260425_UART\uart.v`의 `uart_rx/uart_tx`다. 위 파일들은 로컬 이력이며 Git clone 후 없어도 본 규약을 적용할 수 있도록 필요한 규칙을 여기에 명시했다.

VGA 상태 이름·버튼 우선순위·포트 배치·Basys 보드 조건은 가져오지 않는다. `S_ERROR`/`S_OPEN`의 특정 이름이나 별도 package를 강요하지 않는다. FSM은 MFCC 모듈의 실제 계약에 맞는 상태와 복구 동작을 정의한다.

| 대상 | 적용 |
|---|---|
| 신규 MFCC 제어·연산 RTL 및 새 wrapper | 본 규약 전체 적용 |
| 순수 조합 모듈·배선 전용 top | 조합·연결 규칙 적용. 불필요한 순차 블록/FSM은 만들지 않음 |
| 기존 FFT 재사용 코드 | E-FFT 범위의 기존 스타일 보존. 수치·인터페이스·검증 규칙은 적용 |
| 외부 GitHub 코드·vendor 생성 IP | 원본/생성 코드의 문법은 보존. 신규 wrapper는 본 규약 적용. 출처와 변경 구분 |
| 테스트벤치·Python/C 수치모델·생성 스크립트 | RTL 문법 금지의 대상 아님. 별도 검증 규칙 적용 |

**E-FFT 적용 범위:** `D:\2610_MFCC\reference_code\previous_fft\rtl`의 보존 원본과 그 hash/출처를 기록한 작업 복사본. 기존 `for`, generate-for, 함수, ROM 초기화, 명명, 리셋 구조를 스타일 변경만을 이유로 다시 쓰지 않는다. 이는 해당 코드가 모두 기능 검증됐다는 뜻이 아니다. 특히 AXI wrapper의 backpressure 지원을 코어의 비트 일치 결과로 대신하지 않는다. 수정한 동작은 영향 범위를 따로 검증하며, 신규 모듈에 예외를 자동 확장하지 않는다.

## 2. 문법과 금지 사항

- 신규 HDL은 `.sv`, `logic`, `always_ff`, `always_comb`를 사용한다. clocked block은 `<=`, 조합 block은 `=`를 사용한다.
- 합성 대상에서 시뮬레이션 시간 지연 `#`, `wait`, 런타임 실수 `real/shortreal` 연산, `$ln/$sin/$cos` 같은 시뮬레이션 수학 계산, 파일 입출력으로 동작을 대신하는 코드를 쓰지 않는다.
- 신규 RTL에서 **`for`(generate-for 포함), 사용자 정의 `function`, `task`, `initial`을 금지**한다. `while/repeat/forever`로 같은 금지를 우회하지 않는다. 컴파일 상수인 `$clog2/$bits`는 허용하되 0비트 폭·불법 parameter 조합을 막는다.
- `generate if/case`까지 일괄 금지하지 않는다. 정적인 parameter 선택에만 사용하고 각 지원 설정을 검증한다.
- `.*`와 순서 기반 포트 연결을 금지한다. 모든 포트를 `.port(signal)`로 연결한다. 의도한 미연결 출력은 `.port()`와 설명을 남긴다.
- 신호 하나는 한 driver만 가진다. 다중 procedural driver, assign과 procedural의 중복 구동, 조합 루프, 의도하지 않은 래치를 금지한다.
- 일반 로직으로 clock를 게이팅하거나 카운터 비트를 내부 clock로 쓰지 않는다. clock enable을 사용한다. PS clock/MMCM/PLL 등 전용 자원은 별도 clock 계약과 제약을 따른다.
- `casex`, 결과를 좋게 보이게 하는 `X` 대입이나 폭 경고 무시로 잘못된 동작을 숨기지 않는다.

`for/function/initial` 금지는 **사용자가 선택한 작성 제약**이다. 이 문법이 언제나 합성 불가능하다는 뜻은 아니다. 사용자 요구 없이 예외를 스스로 허용하거나, 반대로 곱셈 전체 금지·DSP/BRAM 전체 금지로 확대하지 않는다. RTL의 곱셈을 FPGA DSP로 매핑하는 것은 custom RTL 설계와 양립한다. 자원/지연은 실제 보고서로 확인한다.

## 3. 구조·명명

일반적인 신규 순차 모듈은 다음 순서와 **2-process** 구조를 따른다.

1. module/port 선언
2. 모듈 내부 `typedef enum logic [...]`, `parameter/localparam`
3. `c_state/n_state`, `_reg/_next` 선언
4. 조합 신호와 명시적 `assign`, 하위 모듈 연결
5. `always_ff` 하나: reset 및 `_reg <= _next`
6. `always_comb` 하나: 모든 기본값 → 상태별 동작 → 명시적 우선순위

- 상태 변수는 `c_state`, `n_state`이며 같은 enum type을 쓴다. enum 이름으로 비교·대입한다.
- 일반 레지스터는 `_reg/_next` 쌍이다. 신규 내부 레지스터에 `_q/_d` 접미사를 혼용하지 않는다. 기존 인터페이스의 포트명은 이름 통일 때문에 바꾸지 않는다.
- 순차 블록의 reset 이외 조건은 조합 블록에서 결정하고, 일반 레지스터의 순차 else는 `_reg <= _next` 대입으로 둔다.
- 모든 `_next`와 조합 출력에 block 시작 시 기본값을 준다. 유지할 값은 `_next = _reg`, 1클록 펄스의 next 기본값은 0이다.
- `case`에는 `default`를 두며 불법 상태의 안전 복구·출력 무효화 정책을 계약에 적는다. VGA의 특정 상태 이름을 그대로 사용하지 않는다. RTL default만으로 물리적 SEU 복구가 증명됐다고 주장하지 않는다.
- 등록 출력은 `assign o_xxx = xxx_reg;`, 1클록 펄스 출력은 등록해서 낸다. `valid`는 펄스와 구분하며, stall 중에는 계약에 따라 유지한다.
- 인스턴스명은 `U_`와 대문자를 사용한다. 포트·콤마는 읽기 쉽게 정렬한다. 주석은 역할, 배율, 잘라내는 비트, 우선순위처럼 비직관적인 부분을 설명한다.

메모리 배열은 개별 일반 레지스터와 다르다. BRAM을 만들기 위해 전체 배열의 `_next` 복사본이나 전 원소 reset을 만들지 않는다. 신규 RAM/ROM이 위 구조·금지 사항과 충돌하면 메모리 primitive/추론 방식, 자원 영향, 필요한 예외를 먼저 문서화하고 사용자 요구와 일치하는 방법을 정한다. 편의만으로 ROM `initial` 예외를 추가하지 않는다. 계수 생성 스크립트의 반복문은 허용되지만, 생성된 합성 RTL도 본 규칙을 충족해야 한다.

`always_comb`를 쓴다는 사실만으로 latch-free가 보장되지 않는다. 완전 대입 여부와 lint/합성 결과를 확인한다.

## 4. 클록·리셋·전송

- MFCC 연산 도메인은 공통 규격의 단일 PL clock 제안을 따른다. 100 MHz는 목표이며 보드 실측값이 아니다.
- 신규 MFCC 계산 모듈은 기존 FFT와 맞춘 **동기 active-low `rst_n`**을 기본으로 한다: `always_ff @(posedge clk)` 안의 `if (!rst_n)`.
- VGA의 비동기 active-high reset을 복사하지 않는다. 외부 reset/PS reset은 경계에서 동기 해제 조건과 충분한 reset clock 수를 보장한다. 단순 극성 반전은 reset 동기화를 대신하지 않는다.
- reset 중 미완료 frame, previous sample, counter, accumulator, valid의 처리와 재시작 조건을 명시한다. valid로 가려지는 RAM/ROM 내용은 무조건 초기화하지 않는다.
- 기존 FFT는 reset 후 `cfg_apply`와 설정 수락이 선행되어야 한다. 원본의 실제 설정 계약을 따른다.
- ready/valid 인터페이스의 수락은 `valid && ready`에서만 발생한다. 출력 stall 중 data/index/last/valid가 유지돼야 한다.
- 출력 ready가 없는 FFT를 임의 정지 가능한 AXI stream으로 간주하지 않는다. buffer/credit 설계 전 FIFO 깊이 또는 연속 처리량을 확정하지 않는다.
- 데이터·프레임 번호·bin/계수 번호·valid/last·BFP 지수는 동일한 지연으로 전달한다.

## 5. 고정소수점 필수 계약

모듈 작성 전에 입력·중간·출력의 다음 항목을 표로 기록한다.

`signedness, W(total bits), F(fraction bits), scale/exponent, range, full product width, accumulator width, rounding location/mode, overflow policy, latency, initiation interval`.

- `Q1.15` 표기만 쓰지 않고 W/F와 부호 포함 여부를 명시한다. 값 해석은 기본 `integer * 2^-F`다. 추가 배율/BFP 지수는 따로 기록한다.
- 상수는 폭·부호를 명시한다. signed/unsigned 혼합, part-select의 unsigned화, 암묵적 확장/절삭을 피한다.
- 곱셈·제곱은 피연산자와 결과 폭을 확인하고, 덧셈/누산 carry·guard bit를 확보한다. 원하는 넓은 결과가 좌변 선언만으로 항상 확보된다고 가정하지 않는다.
- 비트를 줄이는 모든 위치에서 round/truncate와 saturation/wrap을 명시하고 모델과 맞춘다. 음수 shift와 tie를 별도 검증한다.
- FFT 내부의 기존 wrap·rounding 정책은 보존한다. 임의 saturation으로 바꾸고 같은 코어라고 부르지 않는다. 정상 음성 경로에서 발생한 overflow는 실패로 기록한다. overflow 검출을 의도한 스트레스 시험은 별도 분류한다.
- 로그 floor는 지수 보정 후 공통 에너지 단위의 `1e-12`와 비교한다. 정수 에너지가 0인지 여부만으로 BFP 경로의 floor를 결정하지 않는다.
- 양자화된 계수·LUT는 한 생성 절차와 manifest로 관리한다. float64 기준 계수, 정수 테이블, RTL 초기값/상수의 출처를 연결한다.

## 6. 검증과 완료 보고

- TB에는 `initial`, 반복문, function/task, 실수 oracle, 파일 I/O를 사용할 수 있다. 합성 fileset과 분리한다. RTL 제한을 Python/C/TB에 적용하지 않는다.
- 고정소수점 모델 vs float64는 알고리즘 오차를 비교한다. RTL vs 같은 수치 계약의 정수 모델은 valid transaction 기준 **비트 일치**를 비교한다. 서로 다른 합격 조건이다.
- frame/bin/계수 순서, 개수, reset, 입력 gap, 지원하는 backpressure, 단독 frame drain, overflow 플래그와 정상 복귀를 검사한다.
- 숫자 정확성만으로 RTL이 합성 가능하거나 timing이 맞는다고 판단하지 않는다. Vivado 2024.2의 구문/elaboration, 영향 모듈 simulation, synthesis/latch/폭 경고와 자원, 필요한 post-route timing을 단계별 기록한다.
- 완료 보고에는 변경 파일, 적용 규칙/기존 예외, 실행한 명령·도구·결과, 미실행 항목을 쓴다. OOC 결과를 전체 MFCC 또는 보드 결과로 확대하지 않는다.

## 7. 기술 근거

프로젝트의 문법 제한과 도구 지원 범위는 별개다. 2026-10-04 확인한 AMD 공식 자료:

- [Vivado 2024.2 Synthesis, UG901](https://docs.amd.com/r/2024.2-English/ug901-vivado-synthesis)
- [레지스터·리셋과 자원 매핑 권고](https://docs.amd.com/r/2024.2-English/ug901-vivado-synthesis/Coding-Guidelines)
- [외부 파일을 이용한 BRAM 초기화 예제](https://docs.amd.com/r/2024.2-English/ug901-vivado-synthesis/Initializing-Block-RAM-From-an-External-Data-File-Verilog)

기존 FPGA ROM 초기화의 합성 지원은 신규 RTL의 `initial` 금지를 자동 해제하지 않는다. 이 규약은 HDL 문법 검사기의 대체물이 아니며 실제 합성·기능 검증을 생략할 근거가 아니다.
