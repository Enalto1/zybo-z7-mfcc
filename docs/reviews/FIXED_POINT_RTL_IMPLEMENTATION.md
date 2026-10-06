# 정수 MFCC 구현·검증 기록

기준 환경은 Vivado2024.2, `xc7z020clg400-1`이며 보드 다운로드·실행은 수행하지 않는다. 모델/계약 발행, RTL simulation, synthesis, place/route, numerical accuracy는 독립 상태다. 실행 경로는 모두 `D:/2610_MFCC/build/` 아래 새 폴더다.

## 최종 완료 상태 (2026-10-04)

PCM 전처리·window·정수 BFP·FFT20·Power40·Mel60·정수 log·DCT13의 Python 정수 모델과 통합 RTL을 구현했다. 전체 behavioral RTL 비트/프로토콜 검증과 합성·배치·배선·정적 타이밍이 완료됐다. 수치 정확도는 미합격이며 보드 실행과 post-route timing simulation은 수행하지 않았다.

| 구분 | 최종 증거와 결과 |
|---|---|
| 전체 RTL simulation | `fixed_full_rtl/full_616_final_20261004_06`: 개발534+합성82=616프레임,24입력. 불일치0 |
| 비교 수량 | frontend315,392; FFT315,392복소값(모든512bin); Power158,312; Mel/log각16,016; MFCC8,008 |
| 프로토콜 | 입력gap·연속프레임·모든clip 완료,173PCM 입력 중 reset·FFT 진행 중 reset,출력stall15,457cycle(5,000cycle 연속 포함),14,410,168cycle 실행 |
| 재현용 최종 프로젝트 | `fixed_full_rtl/full_616_repro_20261004_07/project/fixed_full.xpr`. RTL/TB/계수/입력·정답 hash가 같은06의 simulation을 명시적으로 재사용하고,07에서 합성·배치·배선을 다시 수행 |
| 합성 자원 | LUT5,775 / FF2,388 / DSP40 / RAMB36×9+RAMB18×7=12.5tile / latch0 |
| 배치·배선 자원 | LUT5,455 / FF2,391 / DSP40 / BRAM12.5tile / latch0 |
| 정적 타이밍 |100MHz,입출력delay각2ns OOC. WNS+0.506ns/WHS+0.097ns,TNS/THS0,제약 검사12종 모두0 |
| 독립 감사 | `fixed_full_rtl/full_616_repro_20261004_07_audit.json`:3,065검사 PASS. 프로젝트 참조26개,현재 RTL/TB/provenance26개,원본FFT13개,437개 계약artifact,1,586,734개 모델/계약 정수성분을 확인 |
| 정확도 | 개발 max0.0551715024552/RMSE0.00626024471527; 합성 max6.591594298125/RMSE0.894057095442; floor 회귀60건. **NOT_ACCEPTED** |

Frontend pre-ring/window,FFT reorder,Power,DCT log 버퍼가 실제 BRAM으로 매핑됐다. 새 frontend/backend/tail의 LUTRAM은0이다. 전체 LUTRAM894개와 routed SRL65개는 보존된 FFT 내부 delay/ROM에 있으므로 전체 메모리가 BRAM이라고 해석하지 않는다. routed BRAM cell 목록과 계층별 자원 보고서를 보존했다. DRC error0이며 DPIP-1×11,DPOP-1×9,DPOP-2×30,ZPS7-1×1의 경고51개를 남겼다. OOC의 실제외부핀·clock배치·PS연결은 검증하지 않았다.

06은 수치·타이밍 증거가 유효하지만 저장 `.xpr`의 root 계수 두 경로가 실행 후 존재하지 않았다. 삭제 원인은 확인하지 못했다.07은 계수를 보존 source snapshot으로 참조하도록 Tcl만 수정했다. 두 계수의 SHA와 합성 read 성공,원본 simulation의 실제 계수 사본을 감사했다. 처음 감사의 경로 가정 실패와06의 감사 결과도 보존하며, 재현 프로젝트는07을 사용한다.

07 manifest SHA-256: `6b29205e6d077682c5e5484c3021ef1366ace357fa07208e952c70e3640a1325`; artifact manifest: `f9c5177e300dc466afa653c4dbbc4f13e09f750a4125dba70d4f1cb759908eb3`; 최종 감사: `3ede40f4c0957000d0dfee3329f5a226d819b26cd49d4100adaedb2e9ec17d0d`.07의60개 artifact와 재사용한06의125개 artifact를 모두 검사했다. 아래 중간 실행은 실패 원인과 변경 근거를 보존한 이력이다.

## 수치 계약과 모델

- `fixed_contract/v1_fft20_power40_mel60_20261004_r2`: FFT→Power→Mel,634프레임 전체512 complex bins, 복소 overflow1개와 복구 포함. 발행 전7,136검사, 발행 marker를 포함한 재검사7,144검사 PASS.
- `fixed_contract/v2_pcm16_mfcc40_20261004`: 전체 PCM→MFCC,24입력616프레임,1,586,734정수 원소 재계산. 발행 전2,293검사, 발행 marker 포함2,301검사 PASS. log 경계214개, 정수 BFP 경계75개.
- **현재 C 인계본** `fixed_contract/v2_pcm16_mfcc40_20261004_r2`: 이전 v2의 nested Mel coefficient 경로 오류를 정정했다. 기존426개 수치 파일은 byte 동일하다. 재귀 descriptor415개·artifact437개 포함 발행 전7,099/후7,107검사 PASS. 이전 발행본은 보존한다.
- 전체 정수 모델 `fixed_full_model/integer_02_20261004`: 개발 음성534프레임과 합성82프레임. runtime float 없음. 개발 max0.0551715024552/RMSE0.00626024471527, 합성 max6.591594298125/RMSE0.894057095442. floor 회귀60건 유지. 정확도는 **미합격/허용 기준 미정**이다.

원인·전후 형식은 [설계 이력](../FIXED_POINT_DESIGN_HISTORY.md), C의 signed shift/넓은 log 곱 처리와 저장 형식은 [포팅 안내](FIXED_POINT_PORTING_NOTES.md)를 따른다. 발행된 계약의 RTL 상태는 발행 시점 기록이며 이후 증거로 해당 파일을 덮어쓰지 않는다.

## 실제 구현 모듈

| 모듈 | 동작·주요 계약 |
|---|---|
| `hardware/fixed/fft/fft20_stream_top.sv` 및 작업 복사본 | E-FFT 출처13파일 SHA 보존.20/F19 데이터, twiddle16/F15, guard21/22, 곱38/합39, 단계별 RNE+wrap, 물리S9, 자연순서512개 |
| `fixed_spectral_top.sv` | q16 입력×16 승격; 첫 입력 전에 tail 예약. ready 없는 FFT 출력을 정지시키지 않음 |
| `power_mel/fixed_spectral_tail.sv` | Power40, Mel60; XPM BRAM에257 Power/6682계수 저장. metadata와stall 정렬 |
| `frontend/mfcc_fixed_frontend.sv` | 연속PCM preemphasis32/F30,512/160 중첩frame,window32/F30,정수BFP,q16; ready로 입력 정지 |
| `log_dct/mfcc_fixed_log.sv` | 정확한 floor 지수 비교,30회 정수 log2 iteration,ln2 고정 상수 곱,log30/F24 |
| `log_dct/mfcc_fixed_dct.sv` |26→13, 계수31/F30, signed64 누산, MFCC40/F24 |
| `log_dct/mfcc_fixed_log_dct.sv` |log/DCT ready·valid와frame/index/s/last/error 연결 |
| `mfcc_fixed_top.sv` |PCM→MFCC 통합; clip-start abort, 마지막 출력 수락 후 clip-done, 출력stall 지원 |

신규 순차 모듈은1 always_ff+1 always_comb, 동기 active-low reset이다. 신규 for/generate-for/function/task/initial/실수 연산은 없다. 조합 계수 표와 배선 전용 wrapper에는 불필요한 순차 블록을 두지 않는다. RAM은 전체 reset/next 복사를 하지 않는다. 메모리별 포트 구조·용량·read latency·in-flight 근거는 `hardware/fixed/README.md`에 적었다. `verification/fixed/check_rtl_rules.py`는 금지 문법·2process·원본hash·정수 모델 AST를 검사하며 실제 Vivado 검증을 대체하지 않는다.

## 실행 증거

### FFT20

`fixed_fft/fft20_directed_02`:41경계프레임,20,992복소bin(41,984정수성분), overflow8개 모두 일치. partial173샘플 reset,입력gap,연속frame,overflow 후정상,마지막drain 확인.100MHz OOC route WNS+0.147ns/WHS+0.119ns지만 reorder LUTRAM fallback으로 버퍼 BRAM 요구를 충족하지 못해 최종 구현으로 채택하지 않았다.

`fixed_fft/fft20_bram_03`:reorder를2048x40 단일 simple-dual-port로 수정했다.41프레임 비트 일치와 이전 출력cycle 유지 확인. 실제 합성에서 reorder RAMB36E1×2+RAMB18E1×1, LUTRAM0을 확인했다. 전체 합성 LUT4,279/FF1,378/DSP20/RAMB36×5+RAMB18×1이다. 최종 routed LUT3,950/FF1,378/DSP20/RAMB36×5+RAMB18×1,100MHz WNS+0.156ns/WHS+0.121ns,setup/hold 실패0이다. OOC I/O delay1ns이며 no_clock/no_input_delay/no_output_delay/unconstrained endpoint0이다. DRC error0,경고 DPOP-2×10/ZPS7-1×1을 보존했다.

### Power/Mel

`fixed_power_mel/tail_full_20261004_01`:621프레임=정상616+경계5. Power159,597개/Mel16,146개 비트 일치, stallhold28,232회, capture/outputstall reset와 malformed frame 복구3검사 PASS. 긴 출력stall을 포함한 최대 frame 처리26,341cycle이다. FFT 출력 upper255 bins를0으로 채운 단위 시험이며 전체 FFT 계산 검증을 대신하지 않는다.

실제 합성 LUT336/FF212/DSP4/BRAM5.5tile이다. Mel ROM RAMB36×4+RAMB18×1, Power RAM RAMB18×2가 매핑됐다. 최종 routed LUT333/FF215/DSP4/BRAM5.5tile, LUTRAM0/latch0이다. 내부100MHz route WNS+0.789ns/WHS+0.193ns,TNS/THS0이다. 이 단위 OOC는 외부95입력/160출력 delay를 제약하지 않았으므로 통합 타이밍이나 I/O 합격을 뜻하지 않는다. DRC error0,경고15개(DPIP7,DPOP-1×3,DPOP-2×4,ZPS7-1×1)는 보고서에 남겼다. `tail_full_20261004_01_audit.json`이73개 산출물 hash와 실제보고서를 확인한다.

후속 `fixed_power_mel/tail_pipeline_full_20261004_02`는 입력·제곱 register와2cycle metadata 정렬을 추가한 최종 tail이다.621프레임 Power159,597개/Mel16,146개,stall29,610cycle,reset/malformed 복구4검사(early-last 추가)가 일치했다. routed LUT330/FF359/DSP4/BRAM5.5tile,LUTRAM0/latch0,내부100MHz WNS+0.638ns/WHS+0.133ns,TNS/THS0이다. 이 단위 역시 외부 delay 미제약이며 통합의 별도 결과로 보완한다.

### 전체 정수 frontend/log/DCT 및 통합

`fixed_spectral/spectral_full_03`의 통합 simulation은634프레임 FFT324,608complex/Power162,938/Mel16,484개 모두 일치했다. 출력stall 안정성190,412cycle,입력backpressure13,559,765cycle,3위치 reset, 잘못된last 검출과reset복구,overflow1프레임 및정상복구를 확인했다. 다만 통합 합성에서 FFTreorder→Power제곱/합산 경로 WNS−2.378ns를 발견해 후속 구현에서 Power 파이프라인을 추가했다. 이 첫 통합 결과를100MHz 합격으로 표시하지 않는다.

최종 spectral `fixed_spectral/spectral_pipeline_full_04`는 동일634프레임 전체값·metadata가 일치했고,stall189,142cycle와입력backpressure13,559,763cycle을 검사했다. routed LUT3,607/FF1,588/DSP24/RAMB36×9+RAMB18×4(11tile),100MHz WNS+0.634ns/WHS+0.050ns다. OOC I/O budget1ns,미제약 내부endpoint0이다. 실제외부핀/clock 위치는 없으므로 보드I/O 타이밍으로 확대하지 않는다. 독립 `spectral_pipeline_full_04_audit.json`이 source23개·artifact35개·계약artifact50개·원본FFT13개 hash와 BRAM 매핑을 확인했다.

정수 log 단위1029건/59,549cycle은 모든27개 floor 지수경계와 잘못된지수/복구를 포함해 일치했다. clean 실행은 `fixed_integer_rtl/log_floor_clean_20261004`다. frontend 단위616프레임315,392개/1,978,674cycle, log/DCT 단위618프레임8034개/1,054,450cycle도 숫자·metadata·stall 검사를 통과했다. frontend clean 재실행은 `fixed_integer_rtl/front_clean_20261004`다. 첫 단위 harness의 launch_simulation(runtime=all) 뒤 중복run all로 완료된 TB를 재실행한 실패도 보존했고, clean replay는 단 한 번 run all 후 종료한다.

초기 frontend 합성은 preemphasis 나눗셈 경로 WNS−14.458ns와 pre-ring LUTRAM fallback을 보였다. 후속 실행에서 수치 결과를 바꾸지 않는 나눗셈 분해·연산 단계 분리·XPM BRAM으로 수정했다. 초기 단위 수치 PASS가 이 합성 문제를 없애지는 않는다.

후속 backend `fixed_integer_rtl/back_07_20261004`는 log경계1,029개(116,193cycle),MFCC8,034개(616정상+2오류/복구프레임,2,160,853cycle)가 일치했다. 합성 LUT786/FF437/DSP11/RAMB18×1,LUTRAM0/latch0,100MHz와I/O2ns 기준 WNS+0.430ns/WHS+0.132ns,TNS/THS0이다. frontend `front_08_20261004`도 window/RNE/abs/peak 단계분리 후616프레임315,392개,3,407,787cycle 비트검사를 통과했다.

최종 frontend `front_11_20261004`는 window 계수를 RAM read와 함께 등록했다.315,392개/3,515,289cycle PASS다. `front_synth_12_20261004`의 LUT1,143/FF364/DSP5/RAMB18×2,LUTRAM0/latch0,내부 최소slack+1.075ns를 확인했다. 단위 설계의 외부 `o_last` OBUF 경로−0.217ns는 실패로 보존한다. 전체 OOC에서 이 신호는 내부 연결이며 위07의 제약된 전체 타이밍은 별도로 PASS다.

PCM→MFCC 초기 smoke3프레임의 모든정수값은 일치했으나 clip 완료 watchdog이 발생했다. `full_completion_debug_20261004_06/progress.log`에서 frontend done이 실제도착하고 frame수도0으로배출되는 반면 종료상태가X로남는 것을 확인했다. 등록된bool만 읽는 중간 수정08도 실패했으므로 `_next` 재참조를 원인으로 단정하지 않는다. 최종 ACTIVE→DRAIN FSM으로 바꾼 `full_completion_fsm_20261004_09`는7입력10프레임에서 모든단계 비트·metadata,stall5,158cycle,partial/FFT진행중 reset,마지막출력과clip-done을 통과했다. 0/511/512/671/672/832샘플 경계를 포함한다. TB의 ready 샘플링도 수락edge 이전으로 명확히 했다. 검증기용 큰 고정 배열은 스트림 파일읽기로 바꾸었지만, 배열읽기가 watchdog의 원인이었다고 판단하지 않는다. 동일 제어 FSM의 전체616프레임 및 구현 검증은 위 최종 실행에서 완료했다.

전체 통합 TB는 PCM입력gap,연속frame요청,173샘플 partial reset,FFT출력 중 reset,출력stall5000cycle,24개clip/0프레임/마지막tail,모든단계정수와frame/s/index/last/error를 검사한다. 전체 비교 범위는616프레임의 frontend315,392개,FFT315,392complex,Power158,312개,Mel/log각16,016개,MFCC8,008개다.

`fixed_full_rtl/full_616_20261004_04`는 위24입력616프레임 모든단계 비교를 완료했다. 불일치0,stall15,485cycle,총14,058,637cycle이며 모든clip 완료를 확인했다. 이는 최초 전체 비트 PASS 증거다. 당시 frontend/log 단위합성은 각각 WNS−3.817ns/−1.891ns였으므로 이 RTL을100MHz 최종품으로 채택하지 않았고 후속 실행에서 파이프라인을 추가했다. full04 XDC에 남아 있던 비호환 `remove_from_collection`도 후속 실행에서 Vivado `get_ports -filter`로 수정했다. 후속 source와 전체 검증 결과는 별도로 보존한다.

full04 route는WNS−2.991ns였고,입력제약도실패해최종타이밍근거로사용하지않는다. `full_616_pipeline_20261004_05`는 올바른2ns I/O제약으로616프레임 모든값이일치했고(stall15,460cycle,14,374,321cycle),routeWNS−0.343ns/WHS+0.070ns였다. 남은window product→peak 경로를분리하고 계수를등록한 후속frontend가 위 최종전체실행에서 통과했다. 각 manifest의 `complete`는 도구실행 종료상태이며 timing합격은slack과별도감사로판정한다.

전체 구현 Tcl `scripts/fixed_full_pipeline.tcl`은100MHz와 입력/출력 delay2ns를 명시한다. 구문/elaboration→simulation→synthesis→opt/place/phys_opt/route→timing/DRC 순서이며 완료 여부는 실행 manifest/체크포인트/보고서로 판단한다. 보드 핀·PS·실제 시스템 I/O 제약은 포함하지 않는다.

## 재현

Python 실행기는 `D:/2610_MFCC/build/python_reference/venv/Scripts/python.exe`다. 새 run-id를 사용한다.

```
python scripts/run_fixed_full_model.py --run-id NEW_MODEL
python scripts/publish_fixed_contract.py --version NEW_SPECTRAL_CONTRACT
python scripts/publish_fixed_full_contract.py --version NEW_FULL_CONTRACT
python verification/fixed/verify_fixed_contract.py <v1_bundle>
python verification/fixed/verify_fixed_full_contract.py <v2_bundle>
python scripts/run_fixed_fft20.py --run-id NEW_FFT --implement
python scripts/run_fixed_power_mel.py --run-id NEW_TAIL --implementation route
python scripts/run_fixed_spectral.py --help
python scripts/verify_fixed_integer_rtl.py --help
python scripts/run_fixed_full_rtl.py --run-id NEW_FULL --implementation route
python -B verification/fixed/audit_full_rtl.py --run <NEW_FULL_directory> --out <new_external_audit.json>
```

각 실행은 소스·정수벡터·SHA·Tcl·도구 로그를 보존한다. 기존 FFT·prec04·실패 실행을 덮어쓰지 않는다. Vivado Windows launcher/하위 도구 시작이 수분씩 지연된 기록은 로그에 남겼으며 simulation 실패와 구분한다. 논문·발표 파일은 저장소에 만들지 않았다. 커밋·푸시하지 않았다.
