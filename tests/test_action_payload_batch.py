import base64
import contextlib
import io
import json
import os
import runpy
import unittest
from unittest import mock


class FakeProcess:
    def __init__(self, lines=None, return_code=0):
        self.stdout = iter(lines or ["문제: 1 / 2\n", "완료: 2 / 2\n"])
        self.return_code = return_code

    def wait(self):
        return self.return_code


class ActionPayloadBatchTests(unittest.TestCase):
    def run_action(self, title, *, start=1, end=2, card_count=2):
        environment = {
            "EVENT_NAME": "workflow_dispatch",
            "INPUT_CLASS_ID": "10",
            "INPUT_SET_ID": "20",
            "INPUT_TITLE": title,
            "INPUT_START": str(start),
            "INPUT_END": str(end),
            "INPUT_CARD_COUNT": str(card_count),
            "INPUT_MODE": "1",
            "INPUT_AMOUNT": "1",
            "CLASSCARD_FAST": "0",
            "CLASSCARD_DEPENDENCIES_READY": "1",
        }
        calls = []

        def fake_popen(arguments, **kwargs):
            calls.append((arguments, kwargs))
            return FakeProcess()

        output = io.StringIO()
        with mock.patch.dict(os.environ, environment, clear=True), \
                mock.patch("subprocess.Popen", side_effect=fake_popen), \
                contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path("action_payload.py", run_name="__main__")
        self.assertEqual(stopped.exception.code, 0)
        return calls, output.getvalue()

    @staticmethod
    def payload_from_call(call):
        arguments = call[0]
        encoded = arguments[arguments.index("--payload-base64") + 1]
        return json.loads(base64.urlsafe_b64decode(encoded + "===").decode("utf-8"))

    def test_full_range_runs_selected_modes_in_parallel(self):
        calls, output = self.run_action("[CLJ-MODES:1,2,3]Vocabulary")
        self.assertEqual([self.payload_from_call(call)["mode"] for call in calls], [1, 2, 3])
        self.assertTrue(all(self.payload_from_call(call)["title"] == "Vocabulary" for call in calls))
        self.assertIn("병렬 실행", output)
        self.assertIn("[암기]", output)
        self.assertIn("[리콜]", output)
        self.assertIn("[스펠]", output)

    def test_partial_range_uses_safe_sequential_path(self):
        calls, output = self.run_action(
            "[CLJ-MODES:1,2]Vocabulary", start=2, end=3, card_count=4
        )
        self.assertEqual([self.payload_from_call(call)["mode"] for call in calls], [1, 2])
        self.assertIn("순차 실행", output)

    def test_single_mode_marker_stays_compatible(self):
        calls, output = self.run_action("[CLJ-MODES:2]Vocabulary")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.payload_from_call(calls[0])["mode"], 2)
        self.assertNotIn("병렬 실행", output)

    def test_test_mode_cannot_be_combined(self):
        environment = {
            "EVENT_NAME": "workflow_dispatch",
            "INPUT_CLASS_ID": "10",
            "INPUT_SET_ID": "20",
            "INPUT_TITLE": "[CLJ-MODES:1,4]Vocabulary",
            "INPUT_START": "1",
            "INPUT_END": "2",
            "INPUT_CARD_COUNT": "2",
            "INPUT_MODE": "1",
            "INPUT_AMOUNT": "1",
            "CLASSCARD_FAST": "0",
            "CLASSCARD_DEPENDENCIES_READY": "1",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(RuntimeError, "테스트는 다른 학습 모드"):
                runpy.run_path("action_payload.py", run_name="__main__")

    def test_fast_path_finishes_without_starting_browser(self):
        environment = {
            "EVENT_NAME": "workflow_dispatch",
            "INPUT_CLASS_ID": "10",
            "INPUT_SET_ID": "20",
            "INPUT_TITLE": "[CLJ-MODES:1,2]Vocabulary",
            "INPUT_START": "1",
            "INPUT_END": "2",
            "INPUT_CARD_COUNT": "2",
            "INPUT_MODE": "1",
            "INPUT_AMOUNT": "1",
            "CLASSCARD_ID": "login",
            "CLASSCARD_PASSWORD": "password",
        }

        def fake_run(payload, modes, login_id, login_password, on_mode_done):
            self.assertEqual(modes, [1, 2])
            self.assertEqual((login_id, login_password), ("login", "password"))
            for mode in modes:
                on_mode_done(mode)

        output = io.StringIO()
        with mock.patch.dict(os.environ, environment, clear=True), \
                mock.patch("fast_learning.run_fast", side_effect=fake_run), \
                mock.patch("subprocess.Popen") as popen, \
                contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path("action_payload.py", run_name="__main__")

        self.assertEqual(stopped.exception.code, 0)
        popen.assert_not_called()
        self.assertIn("초고속 학습이 완료", output.getvalue())


if __name__ == "__main__":
    unittest.main()
