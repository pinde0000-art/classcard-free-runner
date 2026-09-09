"""No Classcard requests or credentials: unit tests plus opt-in local DOM fixtures.

python -m unittest discover -s tests -v
CLASSCARD_BROWSER_TESTS=1 enables headless Chrome tests (Selenium Manager by default).
CLASSCARD_CHROMEDRIVER optionally selects an already installed chromedriver.
"""
import contextlib
import io
import os
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import quote

from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

import dynamic_learning as dynamic
from handler import browser_ops, recall_learning as recall, rote_learning as rote
from handler import spelling_learning as spell, test_learning as test


class WaitTests(unittest.TestCase):
    def test_polling_is_faster_without_shortening_deadline(self):
        wait = browser_ops.LearningWait(Mock(), 15)
        self.assertEqual(wait._timeout, 15)
        self.assertEqual(wait._poll, .1)
        self.assertEqual(browser_ops.LearningWait(Mock(), 15, .02)._poll, .02)

    def test_replaced_button_is_retried(self):
        driver = Mock()
        old, new = Mock(), Mock()
        driver.execute_script.side_effect = [old, StaleElementReferenceException(), new, None]
        result = browser_ops.click_first_available(driver, [('id', 'start')], timeout=1)
        self.assertIs(result, new)

    def test_all_missing_locators_share_one_deadline(self):
        driver = Mock()
        driver.execute_script.return_value = None
        start = time.monotonic()
        with self.assertRaises(TimeoutException):
            browser_ops.click_first_available(driver, [('id', str(i)) for i in range(8)], .21)
        self.assertLess(time.monotonic() - start, .8)
        self.assertLessEqual(driver.execute_script.call_count, 5)

    def test_wait_still_observes_delayed_readiness(self):
        values = iter([False, False, 'ready'])
        self.assertEqual(browser_ops.LearningWait(Mock(), 2, .01).until(lambda _: next(values)), 'ready')

    def test_mixed_question_keeps_text_fallback_after_radio_deadline(self):
        typed = Mock()
        state = {'radios': [], 'hasRadios': True, 'typed': typed, 'choices': []}
        with patch.object(test, 'visible_answer_box', return_value=Mock()), \
             patch.object(test, 'answer_controls', return_value=state), \
             patch.object(test.time, 'monotonic', side_effect=[0, 0, 5]):
            self.assertEqual(test.choose_answer(Mock(), ['apple']), 'apple')
        typed.send_keys.assert_any_call('apple')


class FavoriteCleanupTests(unittest.TestCase):
    def run_fixture(self, subset=False, failure=False, mode=1, progress=0):
        cards = [dict(card_id=str(i), front=f'word{i}', back=f'meaning{i}', favorite=False) for i in (1, 2)]
        driver = Mock()
        driver.find_elements.return_value = [Mock()]
        handler = Mock()
        handler.return_value.run.return_value = 1 if subset else 2
        payload = dict(class_id='1', set_id='2', start=1, end=1 if subset else 2, mode=mode, amount=1)
        names = ['get_account', 'create_login_session', 'make_driver', 'login', 'open_set', 'read_cards',
                 'read_learning_progress', 'prepare_round', 'walk_to_completion', 'set_favorites']
        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(patch.object(dynamic, name)) for name in names}
            mocks['make_driver'].return_value = driver
            mocks['read_cards'].return_value = cards
            mocks['read_learning_progress'].return_value = {'암기': progress}
            mocks['prepare_round'].return_value = False
            mocks['walk_to_completion'].return_value = 100
            if failure:
                mocks['set_favorites'].side_effect = [RuntimeError('partial write'), None]
            stack.enter_context(patch.object(dynamic.time, 'sleep'))
            stack.enter_context(patch.dict(dynamic.MODES, {mode: ('테스트' if mode == 4 else '암기', 'Memorize', handler)}))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            if failure:
                with self.assertRaisesRegex(RuntimeError, 'partial write'):
                    dynamic.run(payload)
            else:
                dynamic.run(payload)
            return mocks['set_favorites'].call_count, driver

    def test_full_set_does_not_reload_to_restore_untouched_favorites(self):
        for mode in (1, 4):
            with self.subTest(mode=mode):
                calls, driver = self.run_fixture(mode=mode)
                self.assertEqual(calls, 0)
                self.assertFalse(any('/set/' in str(call) for call in driver.get.call_args_list))
                driver.quit.assert_called_once()

    def test_subset_and_partial_failure_both_restore(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                calls, driver = self.run_fixture(subset=True, failure=failure)
                self.assertEqual(calls, 2)
                self.assertTrue(any('/set/' in str(call) for call in driver.get.call_args_list))
                driver.quit.assert_called_once()

    def test_already_complete_does_not_write_favorites(self):
        calls, driver = self.run_fixture(progress=100)
        self.assertEqual(calls, 0)
        driver.quit.assert_called_once()


@unittest.skipUnless(os.environ.get('CLASSCARD_BROWSER_TESTS') == '1', 'opt-in headless local fixtures')
class BrowserFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--window-size=1280,900')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--no-sandbox')
        driver_path = os.environ.get('CLASSCARD_CHROMEDRIVER')
        cls.driver = webdriver.Chrome(service=Service(driver_path) if driver_path else Service(), options=options)
        cls.driver.set_page_load_timeout(10)
        cls.driver.set_script_timeout(5)
        cls.commands = 0
        cls.original_execute = cls.driver.execute

        def execute(*args, **kwargs):
            cls.commands += 1
            return cls.original_execute(*args, **kwargs)

        cls.driver.execute = execute

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()

    def load(self, content):
        self.driver.get('data:text/html;charset=utf-8,' + quote(
            '<!doctype html><style>body{font:16px sans-serif}button,a,label,input{display:inline-block;min-width:30px;min-height:24px}.hidden{display:none}</style>' + content))
        type(self).commands = 0

    def test_ordered_fallback_skips_hidden_and_disabled_buttons(self):
        self.load('<div style="opacity:0"><button id="hidden">Hidden</button></div>'
                  '<fieldset disabled><button id="disabled">Disabled</button></fieldset>'
                  '<button id="later" onclick="this.dataset.clicked=1">Start</button>')
        start = time.monotonic()
        selected = recall.click_first_available(self.driver, [
            (By.ID, 'missing'), (By.ID, 'hidden'), (By.ID, 'disabled'),
            (By.XPATH, "//button[@id='later']"),
        ], timeout=.3)
        self.assertLess(time.monotonic() - start, .6)
        self.assertEqual(type(self).commands, 2)
        self.assertEqual(selected.get_attribute('data-clicked'), '1')

    def test_active_card_and_counters_use_single_snapshots(self):
        self.load('<div class="CardItem current deactive">old</div>'
                  '<div class="CardItem current showing" data-idx="new">word</div>'
                  '<span class="known_count">3</span><span class="total_count">24</span>')
        for function in (rote.get_active_card, recall.get_active_card, spell.current_card_element):
            type(self).commands = 0
            card = function(self.driver)
            self.assertEqual(type(self).commands, 1)
            self.assertEqual(card.get_attribute('data-idx'), 'new')
        for function in (rote.sentence_progress, recall.sentence_recall_progress, spell.sentence_spell_progress):
            type(self).commands = 0
            self.assertEqual(function(self.driver), (3, 24))
            self.assertEqual(type(self).commands, 1)

    def test_recall_question_lookup_is_one_command(self):
        self.load('<div id="wrapper-learn"><div class="CardItem current showing">'
                  '<div class="card-top text-normal">apple</div></div></div>')
        self.assertEqual(recall.get_current_question(self.driver, [0, 'apple'], [0, '사과']), 'apple')
        self.assertEqual(type(self).commands, 1)

    def test_recall_substring_ties_keep_original_card_order(self):
        self.load('<div class="CardItem current showing">pick: pear plum</div>')
        self.assertEqual(recall.get_current_question(self.driver, [0, 'pear', 'plum'], [0, '배', '자두']), 'pear')

    def test_recall_sentence_preserves_duplicate_words(self):
        self.load('<div class="CardItem active"><div class="input-box"><span>The</span><span>?</span></div>'
                  '<div class="scramble-body"><a class="btn-scramble clickable">the</a>'
                  '<a class="btn-scramble clickable">pilot</a><a class="btn-scramble clickable">saw</a>'
                  '<a class="btn-scramble clickable">pilot.</a></div></div>')
        answer, remaining = recall.current_sentence_answer(self.driver, [0, 'The pilot saw the pilot.'])
        self.assertEqual(answer, 'The pilot saw the pilot.')
        self.assertEqual(remaining, ['pilot', 'saw', 'the', 'pilot.'])
        self.assertEqual(type(self).commands, 1)

    def test_spell_starts_when_input_appears(self):
        self.load('<div id="wrapper-learn"><button class="btn-opt-start" onclick="this.remove();'
                  'setTimeout(()=>document.querySelector(\'.CardItem\').style.display=\'block\',80)">Start</button>'
                  '<div class="CardItem current showing" style="display:none" data-idx="1">'
                  '<input type="text" name="input_answer"></div></div>')
        start = time.monotonic()
        self.assertEqual(spell.start_spell(self.driver, timeout=3), 'input')
        elapsed = time.monotonic() - start
        print(f'\nSpell fixture ready: {elapsed:.3f}s', flush=True)
        self.assertLess(elapsed, 1.2)

    def test_spell_sentence_snapshot_keeps_prompt_and_tiles(self):
        self.load('<div class="CardItem active"><div class="para_item active">나는 간다</div>'
                  '<a class="scramble-item">go</a><a class="scramble-item">I</a>'
                  '<a class="scramble-item hidden">wrong</a></div>')
        self.assertEqual(spell.sentence_spell_signature(self.driver), ('나는 간다', ('go', 'I')))
        self.assertEqual(type(self).commands, 1)

    def test_typed_question_does_not_wait_for_radio_options(self):
        self.load('<form id="testForm" onsubmit="event.preventDefault();window.submitted=true">'
                  '<div class="box">사과</div><div class="box"><input type="text"></div></form>')
        start = time.monotonic()
        self.assertEqual(test.choose_answer(self.driver, ['apple']), 'apple')
        elapsed = time.monotonic() - start
        print(f'\nTyped-answer fixture: {elapsed:.3f}s (previous fixed wait: 4s)', flush=True)
        self.assertLess(elapsed, 2)
        self.assertTrue(self.driver.execute_script('return window.submitted'))
        self.assertEqual(self.driver.find_element(By.TAG_NAME, 'input').get_attribute('value'), 'apple')

    def test_radio_selection_waits_for_matching_label(self):
        self.load('<div id="testForm"><div class="box"><input type="radio" id="a" name="q">'
                  '<label for="a">pear</label><input type="radio" id="b" name="q">'
                  '<label for="b" id="label" style="display:none">apple</label></div></div>'
                  '<script>setTimeout(()=>document.querySelector("#label").style.display="inline-block",150)</script>')
        self.assertEqual(test.choose_answer(self.driver, ['apple']), 'apple')
        self.assertTrue(self.driver.find_element(By.ID, 'b').is_selected())
        self.assertFalse(self.driver.find_element(By.ID, 'a').is_selected())

    def test_question_and_tile_snapshots_preserve_text_and_case(self):
        self.load('<form id="testForm"><div class="box"><span class="front-hidden">&lt;b&gt;사과&lt;/b&gt;</span></div></form>'
                  '<div id="wrapper-test"><div class="test-sentence-words">'
                  '<a>the</a><a>The</a><a class="clicked">old</a><a style="opacity:0">hidden</a></div></div>')
        _, question = test.visible_question_box(self.driver)
        self.assertEqual(question, test.clean_question('&lt;b&gt;사과&lt;/b&gt;'))
        type(self).commands = 0
        self.assertEqual(test.scramble_choice_texts(self.driver), ['the', 'The'])
        self.assertEqual(type(self).commands, 1)
        type(self).commands = 0
        selected = test.find_scramble_choice(self.driver, 'The')
        self.assertEqual(type(self).commands, 1)
        self.assertEqual(selected.text, 'The')

    def test_native_sentence_click_is_confirmed_by_placed_count(self):
        self.load('<div id="wrapper-test"><section><div class="test-sentence-input"></div>'
                  '<div class="test-sentence-words"><a href="#" onclick="event.preventDefault();'
                  'if(event.isTrusted){this.classList.add(\'clicked\');'
                  'document.querySelector(\'.test-sentence-input\').innerHTML=\'<span>The</span>\'}">The</a></div></section></div>')
        self.assertTrue(test._find_click_confirm(self.driver, 'The', 0))
        self.assertEqual(test.active_scramble_placed_count(self.driver), 1)


if __name__ == '__main__':
    unittest.main()
