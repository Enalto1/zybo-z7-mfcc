# FP32 CE RTL 독립 검토

검토일: 2026-10-04 KST. 범위는 `hardware/fp32/rtl/`의 현재 사용자 작성 SystemVerilog 여섯 파일과 compact Mel 계수 export 변경이다. 소스는 읽기만 했고 이 검토에서 RTL·계수·허용치를 수정하지 않았다. `AGENTS.md`, `CLAUDE.md`, `docs/RTL_CODING_RULES.md`, `docs/MFCC_SPEC.md`와 최종 IP04 계약을 적용했다.

**판정:** 명시된 단일 clock·유한 PCM16·정상 ready/valid·EOF 계약에서 수정이 필요한 새 구조/핸드셰이크 결함은 발견하지 못했다. 이 결론은 정적 검토 범위의 판단이며 전체 MFCC 정확도나 모든 상태의 형식 검증 결과가 아니다. 실제 실행 결과는 제5절에 기록한 범위로 한정한다.

## 1. 정확한 검토 대상

아래는 compact Mel 변경까지 검토한 현재 버전이다. backend와 coefficient generator는 `build/fp32_hw/integration_compact_smoke_01/freeze.json`과 일치한다. 나머지 다섯 RTL은 앞선 `integration_ce_development_01` 고정본과 동일하다. 합계 1,527줄은 주석·공백을 포함한 물리적 줄 수다. 기존 dense 실행의 backend는 별도 SHA256 `0d46ccd639b423fdbfea736f4b224cc0a4c926b945066e5eb0da27200c351577`이며 해당 실행에 현재 코드를 소급 적용하지 않는다.

| 파일 | 줄 수 | always_ff / always_comb | `_reg`/`_next` 쌍 수 | SHA256 |
|---|---:|---:|---:|---|
| `fp32_alu.sv` |233|1 / 1|10|`897534ef8f82551be213c642689b4a05ef46dbdeeb8c0c60c40e05b5256c957f`|
| `fp32_backend.sv` |661|1 / 1|28|`c556a8e85f30b4914ef23c523017a719be2ffc752499be7e25a4ae549201ab6b`|
| `fp32_frame_buffer.sv` |249|1 / 1|11|`3ff29fa15e5d21d58377f7017c161bac8286a615138808c2652fcd0a635932ff`|
| `fp32_mfcc.sv` |120|1 / 1|2|`fa6131f3e4efcaf86754ca37d014046f40c49895551a740ba8ba75fe6d597187`|
| `fp32_preemphasis.sv` |149|1 / 1|8|`5216874feb4454d3daf3605b8696dd8606564112cb7252b2d92904936267eb67`|
| `fp32_window.sv` |115|1 / 1|8|`9b43ea1526df8bd0f7dc58af25678f08ad8d540d14a15a6d34576b4e5dc4c9e9`|

쌍 수 67개에는 각 FSM의 별도 `c_state/n_state` 6쌍을 포함하지 않았다. coefficient generator `verification/fp32/generate_coefficients.py`는194줄, SHA256 `a841f031b956847853edca172504996188d1f3945c516b008a59b82a87f9021f`다. 주석·문자열을 제외한 토큰 검사와 직접 읽기로 다음을 확인했다. 토큰 검사는 HDL parser나 합성을 대신하지 않는다.

- 여섯 FSM 각각 `always_ff @(posedge clk)` 하나, `always_comb` 하나. 모든 reset은 clocked block 안의 `if (!rst_n)`이며 reset 밖 저장은 `_reg <= _next`, `c_state <= n_state`다.
- 조합 블록은 next/출력 기본값으로 시작하며 상태별 변경 뒤 오류 우선순위를 적용한다. 유지 값, 1-clock pulse의 기본 0, sticky 오류 유지가 구분되어 있다.
- 사용자 작성 `for`, generate-for, `function`, `task`, `initial`, `while`, `repeat`, `forever`, `real`, `shortreal`, `casex`, `always_latch`, `.*` 토큰은 각각 0개다. 지연문·파일 I/O·수학 system task·posedge-valid/negedge-valid clock도 없다.
- parameter 선택용 generate-if는 두 곳이다. `fp32_alu.sv:71`의 `INCLUDE_LOG` 선택과 `fp32_frame_buffer.sv:64`의 미지원 DATA_WIDTH 거부다. 현재 지원/검증된 transport 폭은 32뿐이다.
- 모든 실제 인스턴스 포트는 이름으로 연결한다. ECC/event 미사용 출력은 명시적으로 비우며 이유를 주석으로 적었다. 고정 clock의 CE를 제어하고 fabric clock을 만들지 않는다.
- 사용자 RAM 배열 또는 전체 RAM `_next` 복사/reset loop가 없다. `fp32_backend.sv:633`의 일곱 번째 모듈 `fp32_storage`는 XPM 포트/parameter 연결만 하므로 순차/조합 블록이 0개다. 불필요한 FSM을 붙이지 않는 규약의 배선 wrapper 범위에 해당한다. XPM/vendor 내부 문법은 사용자 작성 RTL의 금지 토큰 수에 포함하지 않는다.

## 2. 데이터 수락·보존·종료

| 경계 | 직접 확인한 조건과 근거 |
|---|---|
| PCM → pre-emphasis | `fp32_preemphasis.sv:86`에서 `s_valid && s_ready`일 때만 PCM을 저장한다. 변환·곱·뺄셈을 순서대로 기다리고 `:131`의 filtered 출력 수락 때 previous PCM을 갱신한다. 다음 PCM은 그 뒤에만 수락하므로 stall/gap에서 샘플이 중복 계산되거나 previous가 바뀌지 않는다. `:143`의 clip_clear는 idle인 새 clip에서 previous=0이며 프레임 경계에서는 발생하지 않는다. |
| ALU operands | `fp32_alu.sv:140`에서 연산/피연산자를 저장한다. `:157`에서 A/B/operation 각각의 수락 flag를 따로 기록하고, 수락된 채널의 valid를 내린다. 모든 필요한 flag가 채워진 뒤 결과 대기로 이동한다. `:216`의 response register는 downstream ready가 올 때까지 유지된다. **이 검토는 A/B/operation ready가 임의로 비대칭인 경우를 동적으로 강제했다는 뜻이 아니다.** |
| Frame ring | `fp32_frame_buffer.sv:47`의 쓰기는 COLLECT에서만 발생한다. 최초512회, 이후160회 accepted write 후 READ로 이동한다(`:176`). 쓰기가 멈춘 동안 oldest 주소부터512개를 내보내고352 overlap을 유지한다. 9-bit 주소의 wrap은 의도한 modulo512다. 마지막 출력이 수락되기 전 다음 collection은 시작되지 않는다. |
| Frame output | `fp32_frame_buffer.sv:200`에서 RAM 결과를 data register로 옮긴다. `:205`에서 수락되기 전 data/index/frame/start/last/valid를 바꾸지 않는다. EOF는 read pending/출력 stall 중에도 기록될 수 있으며 이미 확정된 프레임은 끝까지 drain한다(`:244`). |
| Window → FFT | `fp32_window.sv:84`에서 sample과 모든 metadata를 함께 저장한다. ROM 결과와 곱셈 결과를 각각 기다리고 `:111`에서 output을 hold한다. Backend `fp32_backend.sv:278`은 FFT input ready와 상류 ready를 연결한다. 정확한0…511 index와 마지막 beat를 검사하고 같은 frame/start metadata를 유지한다. |
| FFT output | `fp32_backend.sv:309`에서512개 bin을 순서/TLAST와 함께 확인한다.0…256은 내부 register에 저장한 후 power 연산을 끝내고 다음 bin으로 이동한다.257…511도 수락·검사하지만 power에는 저장하지 않는다. FFT를 멈춘 구간에는 output ready도0이다. |
| MFCC output | `fp32_backend.sv:572`에서 계수 결과를 등록하고 `:585`에서 수락된 경우에만 coefficient를 전진한다. C12 수락 후 busy를 내리고 다음 frame config로 돌아간다. `m_last` 자체는 invalid 동안에도 높을 수 있으므로 valid로 한정해서 해석한다. |
| EOF / clip done | `fp32_mfcc.sv:99`는 EOF에 새 PCM보다 우선순위를 준다. 생산자는 마지막 PCM 수락 후 EOF를 내야 한다. pending pre-emphasis가 idle이 될 때 framer EOF를 허용한다. `:107`은 framer done을 기억한 뒤 window/backend busy가 모두 내려갈 때까지 기다린다. 따라서 framer 마지막 읽기와 최종 C12 수락을 혼동하지 않는다. `:113`은 clip_done을 ready까지 보존한다. |

프레임 수는 입력 N<512이면0, 그 외 `1+floor((N-512)/160)`이라는 제어 경로와 일치한다. framer에서 input과 EOF가 같은 cycle에 제시되면 accepted write가 우선하지만, top은 EOF가 있는 동안 PCM ready를 내린다. 두 경계는 다르며 top의 정상 producer 계약에서 충돌하지 않는다.

## 3. BRAM 지연과 계수 주소

모든 authored XPM은 single clock이며 block memory를 지정한다. 프레임 history는 `fp32_frame_buffer.sv:87`에서 READ_LATENCY_B=2, window ROM은 `fp32_window.sv:32`에서 READ_LATENCY_A=2, 공용 storage는 `fp32_backend.sv:652`에서 READ_LATENCY_A=2다.

History는 READ edge에서 요청하고 WAIT를 거쳐 CAPTURE의 다음 edge에서 holding register에 결과를 저장한다. Window 역시 READ→WAIT1→WAIT2→MULTIPLY이며 WAIT2 종료 때 coefficient를 저장한다. Backend ROM/RAM 읽기도 RD_REQ→WAIT1→WAIT2 뒤에 ALU 요청을 낸다. RAM 내부2-cycle latency와 wrapper의 추가 holding/요청 cycle을 같은 것으로 세지 않는다. regce=1로 XPM output register가 진행하는 동안, 새 read 요청을 내지 않으므로 한 요청의 응답만 존재한다.

| 메모리 | 실제 사용 주소 | 선언 크기 | 유효성 보장 |
|---|---:|---:|---|
| history |0…511|512×32|새 clip의 최초 출력 전에512개 모두 다시 쓴다. reset 시 RAM 내용은 지우지 않는다.|
| window ROM |0…511|512×32|프레임 index를 저장해 읽으며 backend가 index/TLAST를 재검사한다.|
| power RAM |0…256|512×32|해당 frame의257개 power를 모두 쓴 후 Mel에서 읽는다.|
| compact Mel ROM |0…458|512×32|26개 필터의 양수 nonzero 계수를 원래 row/bin 오름차순으로 보존한다(`fp32_backend.sv:409`,`:463`).|
| Mel descriptor ROM |0…25|32×32|필터마다 first/last bin과 compact 시작 offset을 읽는다. 필터 번호로 직접 주소 지정한다.|
| log RAM |0…25|32×32|26개 log를 모두 쓴 뒤 DCT 시작. 미작성 padding은 읽지 않는다.|
| DCT cosine ROM |0…337|512×32|13×26 row-major. inner25 뒤 다음 계수로 갈 때 한 번 전진한다(`:551`,`:585`).|
| DCT scale ROM |0…12|16×32|cosine dot product 뒤 scale을 한 번만 곱한다(`:560`).|

XPM의 `rsta`는 output register reset이며 RAM 내용의 전원소 reset으로 해석하지 않았다. Reset 후 write-before-read 제어가 history/power/log의 오래된 내용을 가린다. 현재 구현된 내부 parameter 조합만 검토했으며 `fp32_storage`를 임의 폭/크기로 바꾸는 일반 API까지 검증한 것은 아니다.

Compact 변경은 dense의0계수 위치에서 수행하던 RAM read/주소 순회만 생략한다. 이전 dense RTL도 해당 위치의 multiply/add는 이미 건너뛰었으므로 유지되는459개 MAC의 순서와 피연산자가 동일하다. Descriptor packing은 first=`[8:0]`, inclusive last=`[17:9]`, offset=`[26:18]`, reserved-zero=`[31:27]`다. `fp32_backend.sv:109`는 offset+last-first+1을10-bit로 계산한다. `:414`는 reserved bits, first<=last<=256, exclusive end<=459를 검사하고 위반하면 error11/S_FAULT로 간다. 정상 범위 최대 합은768로10-bit 안에 들어가므로9-bit wrap으로 잘못 통과하지 않는다.

Descriptor도 REQ→WAIT1→WAIT2로 읽은 뒤 bin/offset/end를 등록한다. 다음 MEL_RD_REQ에서 power/weight를 읽으므로 직전 필터의 주소를 쓰지 않는다. 양쪽 읽기 결과는 다음 요청 전에 소비되며 필터마다 accumulator를0으로 초기화한다. 생성기의 contiguous support·consecutive offset 검사는 파일 생성 시에 수행한다. 하드웨어의 범위 검사만으로 임의 변조 descriptor의 수학적 정확성을 보장하지 않으므로 manifest hash가 있는 계수 파일을 사용해야 한다.

`compact_coeff_probe_01`의 파일을 독립적으로 읽어 descriptor를 풀고 frozen C의6682개 Mel word로 다시 펼쳤다. bit mismatch0, 원래 nonzero `(row,bin,word)` 순서459개와 완전 일치, 모든 padding0을 확인했다. 첫 descriptor는 `(first=1,last=3,offset=0,end=3)`, 마지막은 `(210,255,413,459)`다. 여섯 coefficient table의 manifest SHA가 모두 맞으며 기존 window/dense-Mel/DCT-cosine/DCT-scale `.mem` 파일은 dense 실행과 byte 단위 동일하다. dense `mel.mem`은 검토용 사본으로 남고 compact backend에는 연결하지 않는다.

기존 dense `build/fp32_hw/synth_ce_01/reports/utilization_hierarchical.txt`의 hierarchy에서 history/window/power/log/Mel/DCT/scale이 XPM 아래 BRAM으로 연결된 것은 확인했다. 새 compact의 최종 자원·timing은 별도 `synth_compact_01`과 도구 담당의 `hardware/fp32/ip/SYNTHESIS_FLOW.md`를 따른다. dense 합성 자원을 compact 자원으로 바꾸어 적지 않는다.

## 4. CE·리셋·오류

`fp32_preemphasis.sv:29`, `fp32_alu.sv:48`, `fp32_backend.sv:106`은 native vendor `aclken`을 쓴다. reset 중과 해제 후 recovery register가1인 첫 clock은 CE를 켠다. converter input ready, ALU req_ready, FFT config valid는 recovery clock에서 새 산술/config 요청을 받지 않도록 막는다. 새 clip 제어 이벤트와 산술 operand 요청은 구분한다.

활성 converter SEND/WAIT, 선택된 ALU ISSUE/WAIT, FFT CONFIG/FEED/FFT_READ 상태에서만 해당 IP 전송을 처리한다. 이 상태들은 해당 CE가 반드시1인 상태다. 따라서 상태 전이에 쓰인 valid/ready 조건과 CE가 모순되지 않는다. 비활성 IP의 stale ready/valid는 처리하지 않는다. 외부 MFCC 스트림에는 별도 CE가 없고 일반 valid/ready만 적용한다.

[AMD PG109 clock-enable 설명](https://docs.amd.com/r/en-US/pg109-xfft/aclken-Clock-Enable)은 낮은 CE에서 core 상태가 정지함을 명시한다. 실제 `fft_unit_ce_02`는 output 수락 직후와 valid-held 구간을 포함한739개 disabled clock에서 중복/누락 없이2,560개 결과를 검증했다. 해당 unit의 초기 reset은8clock이다. IP 계약의 reset 최소2clock과 통합 시험의16clock을 구분한다. 최소2clock의 모든 위상 조합까지 시험했다고 주장하지 않는다.

global reset은 모든 FSM·pending flag·metadata·accumulator·previous sample·output valid·오류를 초기화하고 FFT config부터 다시 시작한다. `clip_clear`만으로 pending vendor 연산을 취소하지 않는다. 오류 시 backend는 S_FAULT에서 valid를 내리고 busy를 유지하며, recovery는 global reset이다(`fp32_backend.sv:601`). 앞서 수락된 부분 결과까지 소급 취소하는 프로토콜은 없다. 소비자는 error가 있는 실행을 정상 완료로 취급하지 않아야 한다.

내부 window ready는 reset으로 직접 gate하지 않지만 framer valid가 reset에서0이고 window의 저장소/FSM은 동기 reset되므로 top 내부에서 accepted data가 진행하지 않는다. 이는 window를 단독 범용 AXIS IP로 다시 쓰는 경우의 reset 계약까지 승인한다는 뜻은 아니다. 모든 상위 소비자는 reset 동안 valid를 의미 있는 전송으로 해석하지 않고, 충분한 동기 reset을 제공해야 한다.

## 5. 직접 실행된 근거와 남은 범위

검토 시점에 직접 읽은 상태:

- `f0_verified_01`: unchanged framer의 실제 XPM simulation 및 OOC 합성 완료.13개 완료 clip,21,521 checked output word, wrap/EOF/backpressure/reset 포함. 이는 전체 MFCC reset 시험을 대신하지 않는다.
- `f0_long_clip_01`: 변경 없는 framer의 실제 XPM simulation에서 **한 번의 uninterrupted clip**으로85920개 입력 word→534frames→273408개 출력 word를 전부 검사했다. 각 word는 인덱스를 보존하는 injective32-bit identity pattern이며 실제 음성/부동소수점 연산을 수행하지 않았다. 마지막 수락 frame ID533/start85280,128sample tail 폐기,167 write-address wrap/534 read-address wrap,7855 pseudo-random input-gap event를 확인했다. input stall1280835/output stall187260/final-beat stall52067/done hold17clock을 포함하며, 완료 후64clock 동안 stale output/done 없이 idle을 유지했다. Source RTL SHA는 기존 `3ff29f…932ff` 그대로이며 합성은 재실행하지 않았다. 이는 동일 길이의 framer 연속 상태·순서·metadata·EOF 증거이며 **전체 MFCC 단일 clip 처리 성공은 아니다.** 실행 명령: `scripts/run_fp32_f0.py --run-id f0_long_clip_01 --long-clip --simulation-only`. 실제 source snapshot/명령/count는 실행 폴더 `run_manifest.json`에 보존한다.
- `fft_unit_ce_02`: 실제 IP04 FFT unit 통과.5×512출력,739 CE pause clock,고정 수치 허용치 위반0, IP03과 출력 bit mismatch0. 별도의 처음부터 끝까지 MFCC 정확도 주장으로 확대하지 않는다.
- `alu_unit_ce_01/run_manifest.json`: status=`passed`. 이 사실을 임의의 비대칭 A/B/operation-ready 강제 시험 통과로 확대하지 않는다. 독립 수락 flag의 타당성은 위 정적 검토 항목이다.
- `integration_ce_smoke_01/reset_summary.txt`: 실제 vendor simulation에서5단계 abort 후5개 replay 모두13계수 bit 일치 통과. baseline1+replay5프레임, reset low16clock, 총759237 testbench clock. 단계는 converter 수락, framer read17, FFT 마지막 입력 수락, log operand 수락, C12 output stall이며 log 구간에서는 FFT CE가 정지되어 있었다. 함께 실행한832sample smoke도3frame/39계수/247679cycle로 프로토콜을 통과했다. 이는 **dense CE backend**의 reset 증거이며 compact descriptor 오류 주입이나 최소2clock 모든 위상 시험은 아니다.
- `integration_compact_smoke_01`: 최종 compact RTL의832sample/3frame/39계수와10개 수치 단계가 통과했다. `compact_smoke_equivalence_03`에서 dense-CE smoke의10개 파일/7283개 binary32 component word와 bit mismatch0을 확인했다. 이 실행에는 reset stress가 포함되지 않았다.
- `integration_compact_synthetic_01/reset_summary.txt`: 최종 compact RTL의5개 abort 지점과 baseline1/replay5frame,16clock reset 시험이584517clock으로 완료했다. 각 replay의13계수·메타데이터가 baseline과 bit 일치했다. 합성17개 정상 실행의 최종 판정과는 별도 증거다.
- `integration_compact_synthetic_01/comparison.json`: 이후 정상 실행17cases/40frames도 완료했다. 프로토콜 통과와 별도로 수치는12cases 통과/5cases 실패다. `compact_synthetic_equivalence_01`에서 dense-CE와 모든170개 단계 파일의 bit 동일성을 확인했다. 따라서 compact 저장 방식의 동등성과 전체 수치 정확도 통과를 혼동하지 않는다.
- `integration_ce_development_01`: 이전 dense 회로의 단일 clip 실행은18:27KST에 의도적으로 중단했다. 원시 trace와 freeze는 보존했으며 전체 summary가 없어 실행 상태는 `failed`다. `dense_development_final_partial_01`의46/534frame prefix는 전체 clip 성공을 의미하지 않는다. 최종 compact 회로의 전체 음성 수치 검사는 아래 계약을 따르는 별도 `segmented_development_01`에서 완료했다.
- `segmented_development_01`: 최종 compact의8개 실제 vendor worker 모두 exit0,20:10:30KST에 전체 구간 수치 비교를 완료했다.548localframes/90624sample/7124계수를 raw 검사한 뒤534고유frames/6942계수/85920고유sample을 집계했다. 소유권 누락·중복0,7개 유효 경계 frame의8단계 bit 일치, overlap input4704/pre4697개 bit 일치다. Python 및 PC C 대비10개 단계 모두 고정 허용치 위반0/NaN·Inf0이었다. Python 대비 MFCC 최대오차2.70097533111e-4/RMSE2.79105453392e-5. 이는 아래 구간 계약의 실제 완료 결과이며 단일 연속 클립 MFCC 검증·처리율 주장은 아니다.

SEU/불법 enum 강제 주입, 모든 operand-ready 비대칭,32-bit frame/start counter overflow의 동적 실행, 무한 downstream stall 후 진행성의 형식 증명, 새로운 parameter 조합은 미검증이다. 무한 stall은 의도적으로 입력도 정지시키며, 실시간 ADC 손실 없는 수용률은 이 serial backpressure 계약에 포함하지 않는다. 평가20개, 실제 PL 보드 실행, PS/DMA, 전력, board clock/timing 계측은 이번 검토의 증거가 아니다.

## 6. 병렬 구간 재생의 수치 동등성 조건

아래는 구현 전 검토한 검증 계약이며 실행 성공 결과가 아니다. 개발 clip의 논리 프레임0…533을 서로 겹치지 않는 소유 구간 `[a,b]`로 나눈다. 첫 구간의 sample offset O=0, 이후 구간은 `O=160*(a-1)`이다. 마지막 구간은 원본 끝 N=85920까지 입력한다. 그 외 구간은 다음 구간의 첫 목표 프레임도 교차 검사하기 위해 `E=160*(b+1)+512`까지 입력한다. 각 입력 파일은 원본 PCM의 정확한 sample slice `[O,E)`, 즉 byte slice `[2*O,2*E)`여야 하며 수정·재정규화·padding하지 않는다.

처음이 아닌 구간의 local frame0은 문맥 복원용 warmup이다. local sample0만 이전 PCM=0을 쓰기 때문에 원래 clip의 pre-emphasis와 다를 수 있다. `previous_next=current_reg`는 원래 PCM을 보존하므로 local sample1부터는 원래 연속 필터링과 같다. 첫 목표 frame은 local sample160에서 시작하여 그 이전 sample159가 이미 올바르게 처리되었다. 따라서 local frame1 이후의512개 filtered input과 Hamming 위치가 원래 global frame과 같다. 그 뒤에는 frame별 FFT/config,257개 power overwrite,26개 log overwrite, DCT accumulator 초기화가 있으므로 의도된 수학에는 이전 frame의 결과가 포함되지 않는다.

검증기는 먼저 실제 local metadata `(j,160*j,C0…C12,last12)`와 개수·finite·error·drain을 검사한 뒤 `global_F=O/160+j`, `global_start=O+160*j`로 remap한다. 잘못된 local metadata를 remap으로 덮어서는 안 된다. 소유 조건 `a<=global_F<=b`인 프레임만 최종534×13 결과에 한 번 포함한다. nonlast 구간의 `global_F=b+1`은 다음 구간 첫 목표 출력과 bit 비교하는 별도 중복 검사이며 최종 결과에 두 번 넣지 않는다.

Warmup의 전체 FFT/MFCC는 첫 입력이 달라질 수 있으므로 원본이나 이전 구간 끝 프레임과 bit 일치를 요구하지 않는다. 대신 유효한 중복 목표 프레임의 frame/window/FFT/power/Mel/log/DCT/MFCC word를 모두 비교한다. 입력 overlap은672samples=1344bytes로 동일해야 하고, 그 구간의 pre-emphasis는 뒤 구간의 첫 sample 한 개를 제외하고 비교한다. Warmup도 finite·오류·개수·handshake 검사에서는 제외하지 않는다.

K=8일 때 실제 local 출력은534개 소유+7개 warmup+7개 중복=548frames/7124coefficients, 원본에서 잘라 중복 처리한 입력 합계는90624samples다. 최종 고유 결과는534frames/6942coefficients다. 마지막 구간은 마지막 완전 frame 뒤128sample tail까지 포함한다. 각 구간의 원본/슬라이스 SHA, O/E, 소유 범위, warmup/중복 분류, RTL/IP/계수/허용치/TB hash와 실제 local cycle을 남긴다. 비교 기준은 새 구간에서 재생성한 정답이 아니라 고정된 원래 전체 clip의 Python/C 배열이다.

이 방법이 확인하는 것은 **구간 재생으로 전체534개 논리 프레임의 수치를 검증했다는 사실**이다. 실제 RTL frame ID의0…533 연속성, 같은 clip의 장시간 내부 상태·물리 ring 주소 순회, 전체85920sample을 한 번씩 수락하는 EOF 경로, 단일 clip latency/throughput를 대신하지 않는다. 물리 ring 주소·입력 공백·ready pattern·warmup 비용과 local cycle 기준점이 달라지므로 cycle 합산이나 병렬 wall-clock 시간을 전체 clip의 HW 성능으로 쓰지 않는다. 원래 단일 clip 실행의 완료 여부는 별도로 보고해야 한다.
