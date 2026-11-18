# 상세 사용 가이드

[README로 돌아가기](../README.md)

## ID 발급 규칙

- 형식: `<접두어><5자리 숫자>` — 예: `NEW00000`, `90001`
- 접두어: ASCII 영문 대소문자와 숫자만 허용하며 빈 문자열도 가능합니다.
- 최초 기본 시작값: `90001`. 이후에는 상태 파일의 시작값과 마지막 예약 번호를 사용합니다.
- `--id-start`는 시작 번호의 하한값입니다. 기존 예약 번호가 더 크면 그 다음 번호를 사용합니다.
- 재시작 질문에 `y`를 선택하면 `--id-start`보다 우선하여 `00000`부터 시작합니다. 실제 실행 시 번호 상태를 초기화하며 기존 환자를 삭제하지 않습니다.
- 상태 파일은 접두어별로 분리되지 않습니다. 접두어를 바꾸어도 번호 상태를 공유합니다.
- 최대 번호는 `99999`이며, 요청 인원이 남은 범위를 넘으면 실행을 거부합니다.

## 등록 옵션

```powershell
uv run python add_1000_patient_info.py run --execute --id-prefix QA --id-start 91000 --first-name AUTO --last-name TEST --stop-on-failure
```

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| `--execute` | 미지정 | 지정하면 실제 등록, 생략하면 PREVIEW |
| `--id-prefix` | 대화형 입력 | Patient ID 접두어 |
| `--id-start` | 저장값 또는 `90001` | 번호 하한값, `0`~`99999` |
| `--first-name` | `AUTO` | 합성 테스트 이름 |
| `--last-name` | `TEST` | 합성 테스트 성 |
| `--birth` | 빈 문자열 | 생년월일 입력값. 생략하면 UI 기본값 유지 |
| `--description` | 빈 문자열 | 설명 입력값. 생략하면 UI 기본값 유지 |
| `--timeout` | 설정의 `registration_timeout` | 저장 후 ID 확인 제한 시간(초) |
| `--stop-on-failure` | 미지정 | 첫 실패에서 배치 종료 |
| `--retry-delay` | `1.0` | 실패 복구 확인 간격(초) |

Birth와 Description을 입력하려면 설정에 해당 좌표가 있어야 합니다. 포함된 설정에는 두 좌표가 없으므로 먼저 추가해야 합니다. Physician은 `physician_option`이 설정되어 있으면 해당 항목을 선택합니다. 입력은 키 이벤트 기반이므로 기본 예제처럼 영문·숫자 합성 데이터를 사용하고, 특수문자는 실제 화면에서 확인합니다.

## 좌표 및 OCR 설정

모든 좌표는 Moonlight **클라이언트 영역의 왼쪽 위를 원점**으로 합니다. 화면 전체의 절대 좌표가 아닙니다.

| 설정 키 | 형식·용도 |
| --- | --- |
| `client_size` | `[width, height]`. 현재 창 크기와 다르면 실행 거부 |
| `add_patient`, `patient_id`, `first_name`, `last_name`, `ok`, `cancel` | `[x, y]`. 버튼·입력칸 클릭 위치 |
| `birth`, `description` | 선택 입력칸의 `[x, y]` |
| `physician`, `physician_option` | 드롭다운과 선택 항목의 `[x, y]` |
| `dismiss_keypad` | 화면 키패드 닫기 버튼의 `[x, y]` |
| `modal_title_roi` | `NEW PATIENT` 제목 영역 |
| `list_title_roi` | `PATIENT LIST` 제목 영역 |
| `input_values_roi` | ID·이름·성을 포함하는 가로 영역. 클릭 좌표의 중간값으로 세 필드의 OCR 영역을 분리 |
| `list_first_id_roi` | 환자 목록 첫 번째 데이터 행의 ID 영역 |

OCR 영역(ROI)은 `[left, top, right, bottom]` 형식입니다. 현재 화면 크기와 영역을 확인하려면 다음 명령을 사용합니다.

```powershell
uv run python add_1000_patient_info.py screen
uv run python add_1000_patient_info.py calibrate
```

`calibrate`는 안내에 따라 마우스를 위치시킨 뒤 **F8**로 좌표를 수집합니다. 선택 항목은 **F9**로 생략할 수 있습니다. 환자를 저장하지는 않지만 등록 모달을 열며, 종료 후 직접 Cancel로 닫아야 합니다.

> 현재 `calibrate`는 설정 파일을 다시 작성하면서 `input_values_roi`, `cancel`, `list_title_roi`, `dismiss_keypad`를 수집·보존하지 않습니다. 실행 전 기존 설정을 백업하고, 보정 후 이 항목들을 현재 화면에 맞게 추가해야 합니다. 앞의 세 항목은 실제 등록에 필수입니다.

`timing`에서 입력 간격과 대기 시간을 조정할 수 있습니다. 포함된 설정은 다음과 같습니다. 단위는 초이며, 실제 등록 소요 시간을 의미하지 않습니다. 코드에 별도 고정 지연도 존재합니다.

```json
{
  "action_interval": 0.1,
  "typing_interval": 0.002,
  "dropdown_interval": 0.3,
  "ocr_poll_interval": 0.1,
  "modal_timeout": 10.0,
  "registration_timeout": 15.0
}
```

## 진단 및 수동 작업 분석

| 명령 | 사용법 |
| --- | --- |
| `screen` | Moonlight 클라이언트 영역 PNG와 크기를 출력 |
| `input-test` | 직접 `NEW PATIENT` 모달을 연 상태에서 실행. First Name에 `AUTO`를 입력하고 저장 없이 종료 |
| `record` | F8로 수동 작업 기록 시작, F9로 종료. Moonlight가 활성화된 동안의 이벤트만 기록 |
| `record --capture-text` | 문자키를 마스킹하지 않고 기록. 합성 테스트 데이터에만 사용 |
| `analyze` | 최신 기록 세션의 클릭을 설정 좌표와 비교. 40픽셀 이내의 가장 가까운 항목으로 분류 |
| `search-diagnostic` | 검색창에 ID를 입력하고 검색창·결과 영역 OCR 및 화면을 기록. 환자 등록은 수행하지 않음 |

```powershell
uv run python add_1000_patient_info.py input-test
uv run python add_1000_patient_info.py record
uv run python add_1000_patient_info.py analyze
```

검색 진단은 `patient_id`, `--search-point X Y`, `--search-roi L T R B`, `--results-roi L T R B`를 필수로 받습니다. 선택 옵션 `--wait`의 기본값은 1초입니다. 좌표와 영역은 현재 화면에서 측정한 값을 사용합니다.

검색창 OCR이 요청 ID와 다르면 `input_unverified`로 종료합니다. 일치해도 결과는 `observed_unverified`이며, 빈 결과를 포함해 **환자의 존재·부재를 확정하지 않습니다**. 검색 진단은 등록 전 중복 검사에 연결되어 있지 않습니다.

## 복구와 중단

기본 동작은 목표 성공 인원수까지 재시도하는 것입니다.

- 첫 행에서 같은 ID가 확인되면 성공으로 집계합니다.
- 저장 전 실패로 모달이 남아 있으면 Cancel로 닫고 같은 ID로 다시 시도합니다.
- 저장을 시도한 모달이 남아 있으면 입력값을 다시 검증한 뒤 같은 ID로 OK를 재시도합니다.
- 저장 후 결과를 확정하지 못하면 새 ID를 발급하지 않고 확인을 기다립니다.

복구에는 전체 재시도 횟수 제한이 없습니다. 진행이 멈추면 화면과 로그를 확인하고 **Ctrl+C**로 중단합니다. PyAutoGUI FAILSAFE도 활성화되어 있어 마우스를 주 화면 왼쪽 위 모서리로 옮겨 중단을 유도할 수 있습니다. OCR 임시 파일 접근 오류는 세 번 시도 후 예외로 중단합니다.

ID 예약 상태는 저장하지만, 프로세스 종료 후 미확정 등록을 자동 복구하는 기능은 없습니다. 중단 후에는 마지막 ID의 실제 등록 여부를 확인한 뒤 다시 실행합니다.

## 결과 파일

파일은 스크립트가 있는 폴더에 생성됩니다.

| 파일 | 내용 |
| --- | --- |
| `htg_patient_id_state.json` | 마지막 예약 번호와 시작값 |
| `htg_run_results.jsonl` | 시도별 ID, 입력값, 결과, OCR 관측값, 저장 시도 여부, 실패 단계, 단계별 시간 |
| `htg_recovery_results.jsonl` | 복구 과정에서 성공으로 확인한 결과 |
| `htg_batch_results.jsonl` | 목표·시도·성공 건수, 완료 여부, 복구 확인 횟수, 소요 시간 |
| `htg_manual_events.jsonl` | 수동 작업 세션의 클릭·키 이벤트 |
| `htg_search_diagnostics.jsonl` | 검색 입력 및 결과 OCR 관측 기록 |
| `htg_failed_*.png` | 실패 또는 미확정 시점 화면 |
| `htg_moonlight_screen_*.png` | `screen`으로 저장한 화면 |
| `htg_search_diagnostic_*.png` | 검색 진단 화면 |

단일 시도 결과는 `SUCCESS`, `FAIL`, `UNKNOWN`으로 기록됩니다. 복구로 성공한 경우 원래 시도 로그는 그대로 남고 별도 복구 로그가 추가되므로 함께 확인합니다. 현재 시도 로그의 `screenshot_path`는 실제 PNG 생성 여부와 관계없이 `null`로 남을 수 있습니다.

로그·화면 PNG·ID 상태 파일은 `.gitignore`로 제외되어 있습니다.

## 프로젝트 구성 및 검증

| 파일 | 역할 |
| --- | --- |
| `add_1000_patient_info.py` | 창 탐색, 좌표 입력, OCR, 등록·복구, 진단 CLI |
| `htg_coordinates.json` | 화면별 좌표·OCR 영역·타이밍 설정 |
| `test_retry.py` | 재시도·복구·ID 접두어·OCR 오류·검색 진단 등의 단위 테스트 |
| `pyproject.toml`, `uv.lock` | Python 요구사항과 의존성 |
| `run.bat` | 실제 등록 실행 진입점 |
| [GLOSSARY.md](../GLOSSARY.md) | 등록 결과 관련 도메인 용어 |

```powershell
uv run python add_1000_patient_info.py --help
uv run python add_1000_patient_info.py run --help
uv run python -m unittest test_retry.py
```

단위 테스트는 외부 동작을 mock으로 대체합니다. 통과하더라도 실제 Moonlight 화면의 좌표, 원격 입력 전달, OCR 정확도 또는 등록 성공을 보증하지 않습니다. 실제 사용 전 소수의 합성 데이터로 입력과 저장 결과를 확인해야 합니다.

[GLOSSARY.md](../GLOSSARY.md)의 등록 성공 정의에는 등록 전 부재 확인이 포함되어 있지만, 현재 구현은 `pre_save_duplicate_check: not_performed`, `verification_scope: first_list_row_only`를 기록합니다. 실제 검증 범위는 이 문서와 실행 로그를 기준으로 확인합니다.
