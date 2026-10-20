import unittest
from unittest import mock

import fast_learning


SET_HTML = """
<html><body>
  <div class="flip-card" data-idx="101"></div>
  <div class="flip-card extra" data-idx="102"></div>
</body></html>
"""
LEARN_HTML = """
<script>
var c_u = 42;
var current_round = 1;
var is_w_pro = 0;
</script>
"""


class FakeClient:
    def __init__(self, *, response=None):
        self.response = response or {"result": "ok"}
        self.logins = []
        self.posts = []

    def login(self, login_id, login_password):
        self.logins.append((login_id, login_password))

    def get(self, path):
        if path.startswith("/set/"):
            return SET_HTML
        return LEARN_HTML

    def post_json(self, path, data, referer):
        self.posts.append((path, data, referer))
        return self.response


class FastLearningTests(unittest.TestCase):
    def payload(self, **changes):
        value = {
            "class_id": "10",
            "set_id": "20",
            "start": 1,
            "end": 2,
            "card_count": 2,
            "amount": 1,
        }
        value.update(changes)
        return value

    def run_with(self, client, payload=None, modes=None, callback=None):
        with mock.patch("fast_learning.ClasscardHttpClient", return_value=client):
            return fast_learning.run_fast(
                payload or self.payload(),
                modes or [1, 2, 3],
                "login",
                "password",
                callback,
            )

    def test_completes_all_selected_modes_without_browser(self):
        client = FakeClient()
        completed = []

        result = self.run_with(client, callback=completed.append)

        self.assertEqual(result, [1, 2, 3])
        self.assertEqual(client.logins, [("login", "password")])
        self.assertEqual([post[1]["activity"] for post in client.posts], [1, 2, 3])
        self.assertTrue(all(post[1]["card_idx[]"] == ["101", "102"] for post in client.posts))
        self.assertTrue(all(post[1]["score[]"] == [1, 1] for post in client.posts))
        self.assertEqual(completed, [1, 2, 3])

    def test_parser_reads_progress_even_with_void_elements(self):
        parser = fast_learning.SetPageParser()
        parser.feed(
            """
            <div class="bottom-fixed">
              <div class="dp-inline-block">암기 <input value="x"> 100%</div>
              <div class="btn-summary">리콜<br>200%</div>
            </div>
            <p>스펠 999%</p>
            """
        )

        self.assertEqual(parser.progress, {"암기": 100, "리콜": 200})

    def test_sends_only_required_rounds(self):
        client = FakeClient()

        self.run_with(client, payload=self.payload(amount=3), modes=[2])

        self.assertEqual(
            [post[1]["last_round"] for post in client.posts],
            [1, 2, 3],
        )

    def test_partial_range_uses_browser_fallback(self):
        with self.assertRaisesRegex(fast_learning.FastLearningUnavailable, "부분 카드"):
            self.run_with(FakeClient(), payload=self.payload(start=2))

    def test_test_mode_uses_browser_fallback(self):
        with self.assertRaisesRegex(fast_learning.FastLearningUnavailable, "테스트 모드"):
            self.run_with(FakeClient(), modes=[4])

    def test_card_count_mismatch_uses_browser_fallback(self):
        with self.assertRaisesRegex(fast_learning.FastLearningUnavailable, "카드 수"):
            self.run_with(FakeClient(), payload=self.payload(card_count=3, end=3))

    def test_rejected_request_uses_browser_fallback(self):
        with self.assertRaisesRegex(fast_learning.FastLearningUnavailable, "거부"):
            self.run_with(FakeClient(response={"result": "error"}), modes=[1])


if __name__ == "__main__":
    unittest.main()
