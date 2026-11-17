import unittest
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import add_1000_patient_info as app


def result(status, pid="90040", submitted=False):
    return dict(result=status, patient_id=pid, save_attempted=submitted,
                duration_sec=2.8, first_name="AUTO", last_name="TEST")


class RetryTests(unittest.TestCase):
    def batch(self, outcomes, recoveries, count=2, stop=False):
        with ExitStack() as stack:
            stack.enter_context(redirect_stdout(StringIO()))
            reserved = []
            def next_id(*, start, reserve=False):
                candidate = f"{90040 + len(reserved):05d}"
                if reserve:
                    reserved.append(candidate)
                return candidate
            stack.enter_context(patch.object(app, "next_id", side_effect=next_id))
            stack.enter_context(patch.object(app, "load_config", return_value={}))
            stack.enter_context(patch.object(app, "validate_config"))
            stack.enter_context(patch.object(app, "setup_ocr"))
            stack.enter_context(patch.object(app, "log_jsonl"))
            stack.enter_context(patch.object(app.time, "sleep"))
            run = stack.enter_context(patch.object(app, "run", side_effect=outcomes))
            recover = stack.enter_context(patch.object(app, "recover_registration", side_effect=recoveries))
            args = SimpleNamespace(id_start=90001, execute=True, birth="", description="",
                                   retry_delay=1, stop_on_failure=stop)
            summary = app.run_batch(1, args, count)
            self.last_ids = [call.kwargs["patient_id"] for call in run.call_args_list]
            self.last_reservations = reserved
            return summary, run.call_count, recover.call_count

    def test_failures_do_not_consume_objective(self):
        summary, attempts, _ = self.batch(
            [result("FAIL"), result("SUCCESS"), result("FAIL"), result("SUCCESS")],
            ["RETRY", "RETRY"])
        self.assertTrue(summary["completed"])
        self.assertEqual((summary["success"], attempts), (2, 4))
        self.assertEqual(self.last_ids, ["90040", "90040", "90041", "90041"])
        self.assertEqual(self.last_reservations, ["90040", "90041"])

    def test_uncertain_save_is_reconciled_before_next_patient(self):
        summary, attempts, checks = self.batch(
            [result("UNKNOWN", submitted=True), result("SUCCESS", "90041")],
            ["WAIT", RuntimeError("temporary OCR failure"), "SUCCESS"])
        self.assertTrue(summary["completed"])
        self.assertEqual((attempts, checks), (2, 3))
        self.assertEqual(self.last_ids, ["90040", "90041"])

    def test_setup_failure_is_retried(self):
        summary, attempts, _ = self.batch(
            [app.RetryableSetupError("foreground"), result("SUCCESS")], ["RETRY"], count=1)
        self.assertTrue(summary["completed"])
        self.assertEqual(attempts, 2)
        self.assertEqual(self.last_ids, ["90040", "90040"])
        self.assertEqual(self.last_reservations, ["90040"])

    def test_explicit_stop_mode(self):
        summary, attempts, checks = self.batch([result("FAIL")], [], stop=True)
        self.assertFalse(summary["completed"])
        self.assertEqual((attempts, checks), (1, 0))

    def test_manual_interrupt_propagates(self):
        for error in (KeyboardInterrupt(), app.pag.FailSafeException()):
            with self.assertRaises(type(error)):
                self.batch([error], [])

    def test_recovery_does_not_resubmit_confirmed_save(self):
        with patch.object(app.win32gui, "GetForegroundWindow", return_value=1), \
             patch.object(app, "ocr", return_value="90040"), patch.object(app, "click") as click:
            self.assertEqual(app.recover_registration(1, {"list_first_id_roi": []},
                                                     result("UNKNOWN", submitted=True)), "SUCCESS")
            click.assert_not_called()

    def test_submitted_modal_retries_same_id_without_cancel(self):
        cfg = dict(list_first_id_roi=[], input_values_roi=[], ok=[1009, 384], cancel=[855, 384])
        with patch.object(app.win32gui, "GetForegroundWindow", return_value=1), \
             patch.object(app, "ocr", return_value=""), \
             patch.object(app, "validate_input_values"), \
             patch.object(app, "contains_modal_title", return_value=True), \
             patch.object(app, "dismiss_keypad"), patch.object(app, "wait_until", return_value=True), \
             patch.object(app, "click") as click:
            self.assertEqual(app.recover_registration(1, cfg, result("UNKNOWN", submitted=True)), "SUCCESS")
            click.assert_called_once_with(1, cfg["ok"])

    def test_unconfirmed_save_on_list_waits_without_new_submission(self):
        cfg = dict(list_first_id_roi=[], list_title_roi=[])
        with patch.object(app.win32gui, "GetForegroundWindow", return_value=1), \
             patch.object(app, "ocr", side_effect=["90039", "PATIENT LIST"]), \
             patch.object(app, "contains_modal_title", return_value=False), \
             patch.object(app, "click") as click:
            self.assertEqual(app.recover_registration(1, cfg, result("UNKNOWN", submitted=True)), "WAIT")
            click.assert_not_called()

    def test_pre_save_failure_cancels_then_retries_same_patient(self):
        cfg = dict(list_first_id_roi=[], list_title_roi=[], cancel=[855, 384])
        with patch.object(app.win32gui, "GetForegroundWindow", return_value=1), \
             patch.object(app, "ocr", side_effect=["", "", "PATIENT LIST"]), \
             patch.object(app, "contains_modal_title", return_value=True), \
             patch.object(app, "dismiss_keypad"), patch.object(app, "wait_until", return_value=True), \
             patch.object(app, "click") as click:
            self.assertEqual(app.recover_registration(1, cfg, result("FAIL")), "RETRY")
            click.assert_called_once_with(1, cfg["cancel"])

    def test_prefix_is_used_for_preview_and_retries(self):
        args = SimpleNamespace(id_start=90001, id_prefix="TEST", execute=False,
                               restart_ids=False)
        with patch.object(app, "next_id", return_value="90040"), \
             patch.object(app, "run") as run, redirect_stdout(StringIO()):
            app.run_batch(1, args, 1)
            self.assertEqual(run.call_args.kwargs["patient_id"], "TEST90040")

    def test_prefix_validation_and_ocr(self):
        self.assertEqual(app.validate_id_prefix("Ab12"), "Ab12")
        with self.assertRaises(ValueError):
            app.validate_id_prefix("A-B")
        with patch.object(app, "prepare_ocr_image"), patch.object(app, "ocr_image", return_value="Ab1200001"):
            self.assertEqual(app.ocr_patient_id(1, [], "Ab1200001"), "Ab1200001")

    def test_ocr_permission_retries_and_stops(self):
        with patch.object(app.time, "sleep"), self.assertRaises(app.OcrAccessError):
            app.tesseract_call(lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError()), None)

    def test_input_values_reject_wrong_field(self):
        cfg = {"input_values_roi": [0, 0, 300, 50], "patient_id": [50, 25],
               "first_name": [150, 25], "last_name": [250, 25]}
        with patch.object(app, "ocr_patient_id", return_value="NEW00000"), \
             patch.object(app, "ocr", side_effect=["AUTO", "WRONG"]):
            with self.assertRaises(RuntimeError):
                app.validate_input_values(1, cfg, "NEW00000", "AUTO", "TEST")

    def test_search_diagnostic_stops_on_input_mismatch(self):
        args = SimpleNamespace(patient_id="n00001", search_point=[20, 20],
                               search_roi=[0, 0, 100, 40], results_roi=[0, 40, 100, 90], wait=0.1)
        cfg = {"client_size": [100, 100], "list_title_roi": [0, 0, 100, 20]}
        with ExitStack() as stack:
            stack.enter_context(redirect_stdout(StringIO()))
            stack.enter_context(patch.object(app, "load_config", return_value=cfg))
            stack.enter_context(patch.object(app, "client_rect", return_value=(0, 0, 100, 100)))
            stack.enter_context(patch.object(app, "setup_ocr"))
            stack.enter_context(patch.object(app.win32gui, "GetForegroundWindow", return_value=1))
            stack.enter_context(patch.object(app, "ocr", side_effect=["PATIENT LIST", ""]))
            stack.enter_context(patch.object(app, "type_text"))
            stack.enter_context(patch.object(app.time, "sleep"))
            stack.enter_context(patch.object(app, "screenshot_client"))
            log = stack.enter_context(patch.object(app, "log_jsonl"))
            result = app.search_diagnostic(1, args)
            self.assertEqual(result["status"], "input_unverified")
            self.assertIsNone(result["results_ocr"])
            self.assertEqual(log.call_args.args[1]["stage"], "input_ocr")

    def test_search_diagnostic_never_calls_empty_results_absent(self):
        args = SimpleNamespace(patient_id="n00001", search_point=[20, 20],
                               search_roi=[0, 0, 100, 40], results_roi=[0, 40, 100, 90], wait=0.1)
        cfg = {"client_size": [100, 100], "list_title_roi": [0, 0, 100, 20]}
        with ExitStack() as stack:
            stack.enter_context(redirect_stdout(StringIO()))
            stack.enter_context(patch.object(app, "load_config", return_value=cfg))
            stack.enter_context(patch.object(app, "client_rect", return_value=(0, 0, 100, 100)))
            stack.enter_context(patch.object(app, "setup_ocr"))
            stack.enter_context(patch.object(app.win32gui, "GetForegroundWindow", return_value=1))
            stack.enter_context(patch.object(app, "ocr", side_effect=["PATIENT LIST", "n00001"]))
            stack.enter_context(patch.object(app, "type_text"))
            stack.enter_context(patch.object(app.time, "sleep"))
            stack.enter_context(patch.object(app, "screenshot_client"))
            stack.enter_context(patch.object(app, "log_jsonl"))
            stack.enter_context(patch.object(app, "prepare_ocr_image"))
            stack.enter_context(patch.object(app, "tesseract_call", return_value=""))
            result = app.search_diagnostic(1, args)
            self.assertEqual(result["status"], "observed_unverified")
            self.assertEqual(result["results_ocr"], "")

    def test_ask_restart_ids_prompts(self):
        with redirect_stdout(StringIO()), patch("builtins.input", side_effect=["", "n", "NO", "y", "YES", "invalid", "y"]):
            self.assertFalse(app.ask_restart_ids())
            self.assertFalse(app.ask_restart_ids())
            self.assertFalse(app.ask_restart_ids())
            self.assertTrue(app.ask_restart_ids())
            self.assertTrue(app.ask_restart_ids())
            self.assertTrue(app.ask_restart_ids())

    def test_run_batch_restart_ids_starts_from_00000(self):
        with ExitStack() as stack:
            stack.enter_context(redirect_stdout(StringIO()))
            simulated_state = {"last_reserved": 90097}
            def fake_write_json(path, data):
                simulated_state.clear()
                simulated_state.update(data)
            def fake_read_text(self, *args, **kwargs):
                import json
                return json.dumps(simulated_state)
            stack.enter_context(patch.object(app, "write_json", side_effect=fake_write_json))
            stack.enter_context(patch.object(app, "load_config", return_value={}))
            stack.enter_context(patch.object(app, "validate_config"))
            stack.enter_context(patch.object(app, "setup_ocr"))
            stack.enter_context(patch.object(app, "log_jsonl"))
            run_calls = []
            def fake_run(hwnd, args, *, patient_id=None):
                run_calls.append(patient_id)
                return result("SUCCESS", pid=patient_id)
            stack.enter_context(patch.object(app, "run", side_effect=fake_run))
            stack.enter_context(patch.object(app.Path, "is_file", return_value=True))
            stack.enter_context(patch.object(app.Path, "read_text", fake_read_text))

            args = SimpleNamespace(id_start=90001, execute=True, birth="", description="",
                                   retry_delay=1, stop_on_failure=False, restart_ids=True)
            summary = app.run_batch(1, args, count=2)
            self.assertTrue(summary["completed"])
            self.assertEqual(summary["success"], 2)
            # Should have reset state and started at 00000
            self.assertEqual(simulated_state.get("last_reserved"), 1)
            self.assertEqual(simulated_state.get("id_start"), 0)
            self.assertEqual(run_calls, ["00000", "00001"])


if __name__ == "__main__":
    unittest.main()
