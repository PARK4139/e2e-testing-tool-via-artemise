# e2e-testing-tool-via-artemise

Moonlight로 접속한 HTG 화면에서 **합성 환자 데이터를 순차 등록하고 OCR로 결과를 확인하는 Windows UI 자동화 도구**입니다. 클릭 좌표와 화면 인식을 사용하며, 환자 등록 흐름에 특화되어 있습니다.

등록할 인원수를 입력하면 목표 성공 건수까지 실행합니다. 실패 시 같은 Patient ID로 복구·재시도하며, 실행 결과와 단계별 소요 시간을 JSONL로 남깁니다.

> 현재 `SUCCESS`는 저장 후 **환자 목록 첫 행의 ID가 요청 ID와 OCR로 일치했다**는 뜻입니다. 등록 전 중복 검사는 수행하지 않으므로 신규 등록이나 전체 목록의 유일성을 보장하지 않습니다.

## 주요 기능

| 기능 | 동작 |
| --- | --- |
| 환자 일괄 등록 | Patient ID, First Name, Last Name을 입력하고 선택적으로 Birth, Description, Physician을 설정합니다. |
| 저장 전 입력 검증 | ID·이름·성의 각 영역을 OCR로 읽어 기대값과 비교한 뒤 OK를 클릭합니다. |
| 저장 후 결과 확인 | 환자 목록 첫 행의 Patient ID를 OCR로 반복 확인합니다. |
| 동일 ID 재시도 | 실패가 목표 인원수를 소모하지 않도록 같은 ID를 유지하고 복구합니다. |
| 연속 ID 관리 | 영문·숫자 접두어와 5자리 일련번호를 조합하고 마지막 예약 번호를 파일에 저장합니다. |
| 실행 계획 확인 | `--execute` 없이 실행하면 실제 입력·저장·ID 예약 없이 첫 ID와 설정을 출력합니다. |
| 수동 조작 기록·분석 | 클릭 좌표와 키 이벤트를 기록하고 최신 세션의 클릭을 설정된 필드에 대응시킵니다. |
| 화면·검색 진단 | 클라이언트 화면을 캡처하고 검색 입력값 및 결과 영역의 OCR을 기록합니다. |

## 실행 환경 및 설치

- Windows와 Python 3.12 이상
- Python 패키지 관리자 `uv`
- Windows용 Tesseract OCR 및 영어(`eng`) 언어 데이터
- 실행 중인 Moonlight 창과 HTG의 `PATIENT LIST` 화면

PowerShell에서 저장소를 내려받고 의존성을 설치합니다.

```powershell
git clone https://github.com/PARK4139/e2e-testing-tool-via-artemise.git
cd e2e-testing-tool-via-artemise
uv sync
winget install -e --id UB-Mannheim.TesseractOCR
```

Tesseract는 `TESSERACT_CMD` 환경변수, `PATH`, `C:\Program Files\Tesseract-OCR\tesseract.exe` 순으로 찾습니다. 다른 위치에 설치했다면 현재 PowerShell 세션에서 경로를 지정합니다.

```powershell
$env:TESSERACT_CMD = 'D:\Tools\Tesseract-OCR\tesseract.exe'
```

`setup_dependency.bat`는 현재 `uv add pydirectinput`만 실행합니다. 전체 의존성 설치에는 `uv sync`를 사용합니다.

## Quick Start Guide

1. 위의 설치 명령을 실행하고 PowerShell에서 저장소 폴더를 엽니다.
2. Moonlight에서 HTG의 `PATIENT LIST`를 열고 창의 클라이언트 영역을 1280 × 720으로 맞춥니다.
3. `htg_coordinates.json`의 클릭 좌표와 OCR 영역을 현재 화면에 맞춥니다([설정 방법](docs/usage.md#좌표-및-ocr-설정)).
4. `uv run python add_1000_patient_info.py run --execute --stop-on-failure`를 실행합니다.
5. 등록할 인원수에 `1`, 접두어에 사용하지 않은 영문·숫자 조합(예: `QADEMO`), 재시작 여부에 `y`를 입력합니다.
6. 목록 첫 행에 `QADEMO00000`이 표시되고 결과에 `success: 1`, `completed: true`가 나오는지 확인합니다.
7. `uv run python add_1000_patient_info.py run --execute`를 실행하고 인원수 `999`, 같은 접두어 `QADEMO`, 재시작 여부 `n`을 입력합니다.
8. 결과에 `success: 999`, `completed: true`가 나오면 완료입니다. 도중에 중단하려면 Ctrl+C를 누릅니다.

## 상세 문서

- [ID 발급 규칙](docs/usage.md#id-발급-규칙)
- [등록 옵션](docs/usage.md#등록-옵션)
- [좌표 및 OCR 설정](docs/usage.md#좌표-및-ocr-설정)
- [진단 및 수동 작업 분석](docs/usage.md#진단-및-수동-작업-분석)
- [복구와 중단](docs/usage.md#복구와-중단)
- [결과 파일](docs/usage.md#결과-파일)
- [프로젝트 구성 및 검증](docs/usage.md#프로젝트-구성-및-검증)
