
"""HTG Moonlight 환자등록 자동화: 기록 -> 분석 -> 단일 환자등록 -> OCR 검증.

의존성: pyautogui, pynput, pywin32, pillow, pytesseract
추가 설치: Windows용 Tesseract OCR (TESSERACT_CMD 설정 가능)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from threading import Event

import pyautogui as pag
import pydirectinput as pdi
import pytesseract
import win32gui
from PIL import ImageEnhance, ImageGrab, ImageOps, ImageStat
from pynput import keyboard, mouse

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "htg_coordinates.json"
STATE_FILE = ROOT / "htg_patient_id_state.json"
EVENTS_FILE = ROOT / "htg_manual_events.jsonl"
RUNS_FILE = ROOT / "htg_run_results.jsonl"
WINDOW_KEYWORD = "Moonlight"

pag.FAILSAFE = True  # 마우스를 주 화면 왼쪽 위 모서리로 옮기면 중단
DEFAULT_TIMING = {
    "action_interval": 0.2,
    "typing_interval": 0.05,
    "dropdown_interval": 0.5,
    "ocr_poll_interval": 0.5,
    "modal_timeout": 10.0,
    "registration_timeout": 15.0,
}

pag.PAUSE = DEFAULT_TIMING["action_interval"]


def get_timing(cfg: dict) -> dict[str, float]:
    """JSON timing 항목을 검증하고 누락된 값에는 기본값을 적용한다."""
    values = DEFAULT_TIMING.copy()
    raw = cfg.get("timing", {})
    if not isinstance(raw, dict):
        raise ValueError("timing은 JSON 객체여야 함")
    for key, value in raw.items():
        if key not in values:
            raise ValueError(f"지원하지 않는 timing 설정: {key}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"timing.{key}는 0보다 큰 숫자여야 함")
        values[key] = float(value)
    return values


def validate_config(cfg: dict, hwnd: int, *, execute: bool = False,
                    birth: str = "", description: str = "") -> None:
    """기존 좌표는 그대로 사용하고, 실제 실행 전 누락 필드를 확인한다."""
    _, _, width, height = client_rect(hwnd)
    if cfg.get("client_size") is not None and cfg["client_size"] != [width, height]:
        raise RuntimeError(
            f"Moonlight 창 크기 불일치: 설정 {cfg['client_size']} / 현재 {[width, height]}"
        )
    required = ("add_patient", "patient_id", "first_name", "last_name", "ok")
    if execute:
        required += ("modal_title_roi", "list_first_id_roi")
        if birth:
            required += ("birth",)
        if description:
            required += ("description",)
        if cfg.get("physician_option") is not None:
            required += ("physician",)
    missing = [key for key in required if cfg.get(key) is None]
    if missing:
        raise RuntimeError(f"htg_coordinates.json 누락: {', '.join(missing)}")
    for name in ("add_patient", "patient_id", "first_name", "last_name", "ok",
                 "birth", "description", "physician", "physician_option", "dismiss_keypad"):
        pt = cfg.get(name)
        if pt is None:
            continue
        if (not isinstance(pt, list) or len(pt) != 2 or
            any(isinstance(v, bool) or not isinstance(v, int) for v in pt) or
            not (0 <= pt[0] < width and 0 <= pt[1] < height)):
            raise RuntimeError(f"현재 Moonlight 화면 밖이거나 잘못된 좌표: {name}={pt}")
    if execute:
        for name in ("modal_title_roi", "list_first_id_roi"):
            roi = cfg[name]
            if (not isinstance(roi, list) or len(roi) != 4 or
                any(isinstance(v, bool) or not isinstance(v, int) for v in roi) or
                not (0 <= roi[0] < roi[2] <= width and 0 <= roi[1] < roi[3] <= height)):
                raise RuntimeError(f"현재 Moonlight 화면 밖이거나 잘못된 OCR 영역: {name}={roi}")


def stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def write_json(path: Path, data: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def log_jsonl(path: Path, data: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"timestamp": stamp(), **data}, ensure_ascii=False) + "\n")


def find_window() -> int:
    matches: list[tuple[int, str]] = []

    def visit(hwnd: int, _: object) -> None:
        name = win32gui.GetWindowText(hwnd)
        if win32gui.IsWindowVisible(hwnd) and WINDOW_KEYWORD.casefold() in name.casefold():
            matches.append((hwnd, name))

    win32gui.EnumWindows(visit, None)
    if not matches:
        raise RuntimeError("Moonlight 창을 찾지 못함. 먼저 Moonlight를 실행해야 함")
    if len(matches) > 1:
        active = win32gui.GetForegroundWindow()
        matches.sort(key=lambda entry: entry[0] != active)
    hwnd, name = matches[0]
    if win32gui.IsIconic(hwnd):
        raise RuntimeError("Moonlight 창이 최소화됨. 먼저 창을 복원해야 함")
    print(f"대상 창: {name}")
    return hwnd


def client_rect(hwnd: int) -> tuple[int, int, int, int]:
    x, y = win32gui.ClientToScreen(hwnd, (0, 0))
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    return x, y, right - left, bottom - top


def relative_point(hwnd: int) -> list[int]:
    x, y, width, height = client_rect(hwnd)
    mx, my = pag.position()
    rx, ry = mx - x, my - y
    if not (0 <= rx < width and 0 <= ry < height):
        raise ValueError("마우스 포인터가 Moonlight 클라이언트 영역 밖에 있음")
    return [rx, ry]


def click(hwnd: int, point: list[int]) -> None:
    x, y, _, _ = client_rect(hwnd)
    # Moonlight? ??/?? ?? ????? ????? ????.
    pag.moveTo(x + point[0], y + point[1])
    pag.mouseDown()
    try:
        time.sleep(0.15)
    finally:
        pag.mouseUp()


def crop_region(hwnd: int, roi: list[int]):
    x, y, _, _ = client_rect(hwnd)
    left, top, right, bottom = roi
    return ImageGrab.grab(bbox=(x + left, y + top, x + right, y + bottom), all_screens=True)


def make_roi(first: list[int], second: list[int]) -> list[int]:
    x1, x2 = sorted((first[0], second[0]))
    y1, y2 = sorted((first[1], second[1]))
    if x2 - x1 < 15 or y2 - y1 < 10:
        raise ValueError("OCR 영역이 너무 작음. 좌상단/우하단을 더 넓게 지정해야 함")
    return [x1, y1, x2, y2]


def setup_ocr() -> None:
    executable = os.getenv("TESSERACT_CMD") or shutil.which("tesseract")
    if not executable:
        fallback = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        executable = str(fallback) if fallback.is_file() else None
    if not executable:
        raise RuntimeError("Tesseract OCR 미설치: 설치 후 TESSERACT_CMD 환경변수 설정")
    pytesseract.pytesseract.tesseract_cmd = executable
    pytesseract.get_tesseract_version()


def ocr(hwnd: int, roi: list[int], *, digits_only: bool = False) -> str:
    image = crop_region(hwnd, roi)
    image = ImageOps.grayscale(image)
    if ImageStat.Stat(image).median[0] < 128:
        image = ImageOps.invert(image)  # Moonlight의 어두운 배경에 밝은 글자 대응
    image = ImageOps.autocontrast(image)
    image = ImageEnhance.Contrast(image).enhance(1.5)
    image = image.resize((image.width * 3, image.height * 3))
    settings = "--psm 7"
    if digits_only:
        settings += " -c tessedit_char_whitelist=0123456789"
    value = pytesseract.image_to_string(image, config=settings, lang="eng")
    return re.sub(r"\D", "", value) if digits_only else value.strip()


def contains_modal_title(hwnd: int, cfg: dict) -> bool:
    value = ocr(hwnd, cfg["modal_title_roi"])
    return "NEWPATIENT" in re.sub(r"[^A-Z]", "", value.upper())


def wait_until(predicate, *, timeout: float = 10.0, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class HotkeyPointPicker:
    """Moonlight가 포커스된 상태에서도 F8로 포인터 좌표 저장. F9로 선택 생략."""

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self.event = Event()
        self.skip = False
        self.listener = keyboard.Listener(on_press=self.on_press)

    def on_press(self, key) -> None:
        if key == keyboard.Key.f8:
            self.skip = False
            self.event.set()
        elif key == keyboard.Key.f9:
            self.skip = True
            self.event.set()

    def __enter__(self):
        self.listener.start()
        return self

    def __exit__(self, *_):
        self.listener.stop()

    def point(self, description: str, *, optional: bool = False) -> list[int] | None:
        while True:
            self.event.clear()
            print(f"마우스를 [{description}] 위치에 올리고 F8" + (" / F9 생략" if optional else ""))
            self.event.wait()
            if self.skip:
                if optional:
                    return None
                print("필수 항목은 생략 불가")
                continue
            try:
                point = relative_point(self.hwnd)
                print(f"  → {point}")
                return point
            except ValueError as exc:
                print(f"  {exc}. 다시 시도")

    def roi(self, description: str) -> list[int]:
        while True:
            first = self.point(f"{description} 영역의 좌상단")
            second = self.point(f"{description} 영역의 우하단")
            try:
                return make_roi(first, second)
            except ValueError as exc:
                print(f"  {exc}. 다시 시도")


def calibrate(hwnd: int) -> None:
    _, _, width, height = client_rect(hwnd)
    print("\n[좌표 보정] Moonlight 창 크기를 이후에도 유지해야 함")
    print("먼저 PATIENT LIST 화면을 띄워야 함. 좌표 지정은 전역 F8 키 사용")
    old = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.is_file() else {}
    cfg: dict = {"client_size": [width, height], "timing": old.get("timing", DEFAULT_TIMING.copy())}

    with HotkeyPointPicker(hwnd) as picker:
        cfg["add_patient"] = picker.point("PATIENT LIST의 사람+ 버튼 중앙")
        cfg["list_first_id_roi"] = picker.roi("목록 첫 번째 데이터 행의 ID 글자만 포함")
        print("사람+ 버튼을 자동 클릭하여 NEW PATIENT 모달을 연다")
        click(hwnd, cfg["add_patient"])
        time.sleep(0.8)
        cfg["modal_title_roi"] = picker.roi("NEW PATIENT 제목 글자만 포함")
        for name, label in [
            ("patient_id", "Patient ID 입력칸"),
            ("first_name", "First Name 입력칸"),
            ("last_name", "Last Name 입력칸"),
            ("birth", "Birth 입력칸"),
            ("description", "Description 입력칸"),
            ("physician", "Physician 드롭다운"),
        ]:
            cfg[name] = picker.point(label)
        print("Physician 메뉴를 연다. 목록의 사용 가능한 항목을 선택해 좌표를 기록")
        click(hwnd, cfg["physician"])
        time.sleep(0.4)
        cfg["physician_option"] = picker.point("Physician 선택할 항목", optional=True)
        if cfg["physician_option"] is not None:
            click(hwnd, cfg["physician_option"])
        else:
            click(hwnd, cfg["patient_id"])
        cfg["ok"] = picker.point("OK 버튼 중앙")

    write_json(CONFIG_FILE, cfg)
    print(f"\n좌표 저장: {CONFIG_FILE}")
    print("아직 환자를 등록하지 않음. 모달은 Cancel로 닫고, PATIENT LIST로 복귀")


def load_config(hwnd: int) -> dict:
    if not CONFIG_FILE.is_file():
        raise RuntimeError("htg_coordinates.json 파일 없음. 수집한 좌표를 JSON에 먼저 저장")
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    get_timing(cfg)  # 설정값 유효성 검증
    return cfg


def next_id(*, start: int = 90001, reserve: bool = False) -> str:
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.is_file() else {}
    candidate = max(start, int(state.get("last_reserved", start - 1)) + 1)
    if candidate > 99999:
        raise RuntimeError("설정한 5자리 Patient ID 테스트 범위 소진")
    if reserve:
        # 실행 시작 전 선점: 크래시/재시도 시 동일 ID 재사용 금지
        write_json(STATE_FILE, {"last_reserved": candidate})
    return f"{candidate:05d}"


 
pdi.PAUSE = 0.01


def type_text(
    hwnd: int,
    point: list[int],
    value: str,
    *,
    interval: float,
) -> None:
    # 입력 필드 활성화
    click(hwnd, point)
    time.sleep(0.3)

    # 입력 포커스 확정
    click(hwnd, point)
    time.sleep(0.2)

    # 기존 입력값 삭제
    pdi.press("end")
    pdi.press("backspace", presses=25, interval=0.01)
    time.sleep(0.1)

    # 새 값 입력
    for char in value:
        if "A" <= char <= "Z":
            pdi.keyDown("shift")
            try:
                pdi.press(char.lower())
            finally:
                pdi.keyUp("shift")
        elif char == " ":
            pdi.press("space")
        else:
            pdi.press(char.lower())

        time.sleep(interval)

    time.sleep(0.1)



 


def keypad_visible(hwnd: int, point: list[int]) -> bool:
    # ?? ?? ??? ?? ??? ?? ???? ????.
    x, y, _, _ = client_rect(hwnd)
    probe = ImageGrab.grab(
        bbox=(x + point[0] - 40, y + point[1] - 5,
              x + point[0] - 20, y + point[1] + 5),
        all_screens=True,
    ).convert("L")
    return ImageStat.Stat(probe).mean[0] > 150


def dismiss_keypad(hwnd: int, cfg: dict) -> None:
    point = cfg.get("dismiss_keypad", [1179, 388])
    for _ in range(3):
        if not keypad_visible(hwnd, point):
            return
        x, y, _, _ = client_rect(hwnd)
        # ?? ???? ?? ??? ???? ??? ??? ????.
        pag.moveTo(x + point[0], y + point[1])
        pag.mouseDown()
        try:
            time.sleep(0.15)
        finally:
            pag.mouseUp()
        time.sleep(0.5)
    if keypad_visible(hwnd, point):
        raise RuntimeError("??? ?? ??: ?? ?? ??? ?? ??? ?????")



def input_test(hwnd: int) -> None:
    """NEW PATIENT의 First Name만 입력한다. 저장/Patient ID 예약 없음."""
    cfg = load_config(hwnd)
    validate_config(cfg, hwnd)
    timing = get_timing(cfg)

    if win32gui.GetForegroundWindow() != hwnd:
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.3)
    if win32gui.GetForegroundWindow() != hwnd:
        raise RuntimeError("Moonlight 창 활성화 실패")

    if cfg.get("modal_title_roi") is not None:
        setup_ocr()
        if not contains_modal_title(hwnd, cfg):
            raise RuntimeError("NEW PATIENT 모달을 확인할 수 없음. 수동으로 모달을 먼저 열어야 함")

    type_text(hwnd, cfg["first_name"], "AUTO", interval=timing["typing_interval"])
    print("INPUT TEST 완료: First Name 칸에 AUTO가 표시되는지 육안 확인")
    print("환자는 저장하지 않음. 모달의 Cancel 버튼으로 종료")


def screenshot_client(hwnd: int, path: Path) -> None:
    x, y, width, height = client_rect(hwnd)
    ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(path)


 
def run(hwnd: int, args) -> None:
    cfg = load_config(hwnd)
    timing = get_timing(cfg)

    validate_config(
        cfg,
        hwnd,
        execute=args.execute,
        birth=args.birth,
        description=args.description,
    )

    pag.PAUSE = timing["action_interval"]

    timeout = (
        args.timeout
        if args.timeout is not None
        else timing["registration_timeout"]
    )

    candidate = next_id(start=args.id_start)

    print(
        f"\n실행 계획: ID={candidate}, "
        f"First={args.first_name}, "
        f"Last={args.last_name}"
    )
    print(
        f"Timing={json.dumps(timing, ensure_ascii=False)}, "
        f"timeout={timeout}"
    )

    if not args.execute:
        print("PREVIEW: 실제 등록 없음")
        return

    setup_ocr()

    # Moonlight 활성화
    if win32gui.GetForegroundWindow() != hwnd:
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(timing["action_interval"])

    if win32gui.GetForegroundWindow() != hwnd:
        raise RuntimeError("Moonlight 창 활성화 실패")

    pid = next_id(
        start=args.id_start,
        reserve=True,
    )

    began = time.monotonic()
    status = "UNKNOWN"
    reason = ""
    actual_id = ""

    try:
        # STEP 1. Add Patient
        print("[1] Add Patient 클릭")

        click(hwnd, cfg["add_patient"])

        if not wait_until(
            lambda: contains_modal_title(hwnd, cfg),
            timeout=timing["modal_timeout"],
            interval=timing["ocr_poll_interval"],
        ):
            raise RuntimeError(
                "NEW PATIENT 모달 확인 실패"
            )

        
        # STEP 2. Patient ID
        print(f"[2] Patient ID: {pid}")
        type_text(
            hwnd,
            cfg["patient_id"],
            pid,
            interval=timing["typing_interval"],
        )

        # STEP 3. First Name
        print(f"[3] First Name: {args.first_name}")
        type_text(
            hwnd,
            cfg["first_name"],
            args.first_name,
            interval=timing["typing_interval"],
        )

        # STEP 4. Last Name
        print(f"[4] Last Name: {args.last_name}")
        type_text(
            hwnd,
            cfg["last_name"],
            args.last_name,
            interval=timing["typing_interval"],
        )

        # STEP 5. Birth
        if args.birth:
            type_text(
                hwnd,
                cfg["birth"],
                args.birth,
                interval=timing["typing_interval"],
            )

        # STEP 6. Description
        if args.description:
            type_text(
                hwnd,
                cfg["description"],
                args.description,
                interval=timing["typing_interval"],
            )

        # STEP 7. Keypad 닫기
        print("[7] Keypad 닫기")
        dismiss_keypad(hwnd, cfg)
        time.sleep(0.5)

        # STEP 8. Physician
        if cfg.get("physician_option") is not None:
            print("[8] Physician 선택")
            click(hwnd, cfg["physician"])
            time.sleep(timing["dropdown_interval"])
            click(hwnd, cfg["physician_option"])

        # STEP 9. OK
        print("[9] OK 클릭")
        dismiss_keypad(hwnd, cfg)
        time.sleep(0.5)
        click(hwnd, cfg["ok"])

        # STEP 10. OCR 등록 결과 확인
        print("[10] OCR 등록 결과 확인")

        end = time.monotonic() + timeout
        observations = []

        while time.monotonic() < end:
            if not contains_modal_title(hwnd, cfg):
                actual_id = ocr(
                    hwnd,
                    cfg["list_first_id_roi"],
                    digits_only=True,
                )

                observations.append(actual_id)

                if actual_id == pid:
                    status = "SUCCESS"
                    reason = "Patient ID OCR 일치"
                    break

            time.sleep(timing["ocr_poll_interval"])

        if status != "SUCCESS":
            reason = (
                "OCR 일치 확인 실패 "
                f"(Expected={pid}, Actual={actual_id})"
            )

    except pag.FailSafeException:
        status = "UNKNOWN"
        reason = "PyAutoGUI FAILSAFE 중단"
        raise

    except Exception as exc:
        status = "FAIL"
        reason = f"{type(exc).__name__}: {exc}"
        print(f"ERROR: {reason}")

    finally:
        if status != "SUCCESS":
            path = (
                ROOT
                / f"htg_failed_{pid}_{int(time.time())}.png"
            )

            try:
                screenshot_client(hwnd, path)
                print(f"스크린샷: {path}")

            except Exception as exc:
                print(f"스크린샷 저장 실패: {exc}")

        record_data = {
            "patient_id": pid,
            "first_name": args.first_name,
            "last_name": args.last_name,
            "result": status,
            "actual_ocr_id": actual_id,
            "duration_sec": round(
                time.monotonic() - began,
                2,
            ),
            "reason": reason,
        }

        log_jsonl(RUNS_FILE, record_data)

        print(
            json.dumps(
                record_data,
                ensure_ascii=False,
                indent=2,
            )
        )

        print(f"실행 로그: {RUNS_FILE}")


def record(hwnd: int, *, capture_text: bool = False) -> None:
    print("\n[수동 작업 기록] Moonlight에서 F8=기록 시작, F9=기록 종료")
    print("문자키는 기본 마스킹(*). --capture-text 사용 시 입력 문자가 저장되므로 테스트 데이터에서만 사용")
    is_recording = Event()
    finished = Event()
    event_count = 0
    start_time = time.monotonic()

    def append(data: dict) -> None:
        nonlocal event_count
        event_count += 1
        log_jsonl(EVENTS_FILE, {"session": session, "elapsed_sec": round(time.monotonic() - start_time, 3), **data})

    def on_key(key) -> None:
        if key == keyboard.Key.f8:
            is_recording.set()
            print("기록 시작")
            return
        if key == keyboard.Key.f9:
            is_recording.clear()
            finished.set()
            return
        if not is_recording.is_set() or win32gui.GetForegroundWindow() != hwnd:
            return
        char = getattr(key, "char", None)
        value = (char if capture_text else "*") if char else str(key)
        append({"event": "key_press", "key": value})

    def on_mouse(x, y, button, pressed) -> None:
        if not pressed or not is_recording.is_set() or win32gui.GetForegroundWindow() != hwnd:
            return
        ox, oy, width, height = client_rect(hwnd)
        if 0 <= x - ox < width and 0 <= y - oy < height:
            append({"event": "click", "button": str(button), "client_xy": [x - ox, y - oy]})

    session = stamp()
    with keyboard.Listener(on_press=on_key), mouse.Listener(on_click=on_mouse):
        finished.wait()
    print(f"기록 종료: {event_count}건 → {EVENTS_FILE}")


def analyze(hwnd: int) -> None:
    cfg = load_config(hwnd)
    if not EVENTS_FILE.is_file():
        raise RuntimeError("수동 작업 로그 없음. record 모드를 먼저 실행해야 함")
    events = [json.loads(line) for line in EVENTS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not events:
        print("기록된 이벤트 없음")
        return
    session = events[-1]["session"]
    current = [row for row in events if row.get("session") == session]
    key_count = sum(row["event"] == "key_press" for row in current)
    print(f"\n최신 수동 세션: {session} | 키 이벤트 {key_count}개")
    point_keys = ("add_patient", "patient_id", "first_name", "last_name", "birth",
                  "description", "physician", "physician_option", "ok")
    click_points = {name: cfg[name] for name in point_keys if cfg.get(name) is not None}
    if not click_points:
        raise RuntimeError("분석용 좌표 없음. htg_coordinates.json 확인")
    for item in current:
        if item["event"] != "click":
            continue
        xy = item["client_xy"]
        nearest = min(click_points, key=lambda key: (click_points[key][0] - xy[0]) ** 2 + (click_points[key][1] - xy[1]) ** 2)
        distance2 = sum((a - b) ** 2 for a, b in zip(click_points[nearest], xy))
        name = nearest if distance2 <= 40**2 else "미분류"
        print(f"{item['elapsed_sec']:7.2f}s  click {xy} -> {name}")


def screen(hwnd: int) -> None:
    """현재 Moonlight 클라이언트 영역을 저장해 OCR ROI 분석에 사용한다."""
    _, _, width, height = client_rect(hwnd)
    target = ROOT / f"htg_moonlight_screen_{datetime.now():%Y%m%d_%H%M%S}.png"
    screenshot_client(hwnd, target)
    print(f"Moonlight client_size: [{width}, {height}]")
    print(f"스크린샷 저장: {target}")
    print("ROI 형식: [left, top, right, bottom] (스크린샷 기준 픽셀)")


def main() -> None:
    parser = argparse.ArgumentParser(description="HTG Moonlight 환자 1명 등록 자동화")
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("calibrate", help="기존 수동 좌표 보정 (선택 사항)")
    sub.add_parser("screen", help="Moonlight 클라이언트 영역 캡처 및 크기 확인")
    sub.add_parser("input-test", help="저장 없이 First Name 필드 키보드 전송 시험")
    recorder = sub.add_parser("record", help="수동 등록 1회 조작 이벤트 기록")
    recorder.add_argument("--capture-text", action="store_true", help="합성 테스트 데이터만 입력할 때 사용")
    sub.add_parser("analyze", help="최신 수동 등록 로그의 클릭 순서 분석")
    runner = sub.add_parser("run", help="환자 1명 등록 및 OCR 검증")
    runner.add_argument("--execute", action="store_true", help="실제 등록 실행 (미지정 시 PREVIEW)")
    runner.add_argument("--id-start", type=int, default=90001, help="독립 테스트 ID 시작값, 기본 90001")
    runner.add_argument("--first-name", default="AUTO", help="합성 테스트 이름")
    runner.add_argument("--last-name", default="TEST", help="합성 테스트 성")
    runner.add_argument("--birth", default="", help="기본값 유지 시 생략, 예: 1986-11-12")
    runner.add_argument("--description", default="", help="미지정 시 UI 기본값 유지")
    runner.add_argument("--timeout", type=float, default=None, help="미지정 시 JSON timing.registration_timeout")
    args = parser.parse_args()
    hwnd = find_window()
    if args.mode == "calibrate":
        calibrate(hwnd)
    elif args.mode == "screen":
        screen(hwnd)
    elif args.mode == "input-test":
        input_test(hwnd)
    elif args.mode == "record":
        record(hwnd, capture_text=args.capture_text)
    elif args.mode == "analyze":
        analyze(hwnd)
    elif args.mode == "run":
        run(hwnd, args)


if __name__ == "__main__":
    main()
