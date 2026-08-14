import random
import re
import time
from collections import Counter
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


START_BUTTON_SELECTORS = [
    (
        By.XPATH,
        "//*[self::a or self::button]"
        "[contains(normalize-space(.), '리콜학습')"
        " and contains(normalize-space(.), '전체구간')]",
    ),
    (By.CSS_SELECTOR, ".btn-opt-start"),
    (By.CSS_SELECTOR, "#wrapper-learn .btn-opt-start"),
    (By.CSS_SELECTOR, "#wrapper-learn a.btn-success"),
    (By.CSS_SELECTOR, "#wrapper-learn a[class*='start']"),
    (By.CSS_SELECTOR, "#wrapper-learn .start-opt-body a"),
    (By.XPATH, "//*[@id='wrapper-learn']//a[contains(normalize-space(.), '시작')]"),
    (By.XPATH, "//*[@id='wrapper-learn']//button[contains(normalize-space(.), '시작')]"),
]

ENTRY_SELECTORS = [
    (By.CSS_SELECTOR, "[href*='/Recall/']"),
    (By.CSS_SELECTOR, "[onclick*='/Recall/']"),
    (By.CSS_SELECTOR, "[data-href*='/Recall/']"),
    (
        By.XPATH,
        "//*[normalize-space(.)='리콜' or normalize-space(.)='리콜학습']"
        "/ancestor-or-self::*[self::a or @onclick][1]",
    ),
]

SENTENCE_CHOICE_SELECTOR = (
    ".CardItem.active .btn-scramble.clickable, "
    ".CardItem.current .btn-scramble.clickable, "
    ".CardItem.showing .btn-scramble.clickable, "
    ".scramble-body .btn-scramble.clickable, "
    ".btn-scramble.clickable"
)

SENTENCE_PLACED_SELECTOR = (
    ".CardItem.active .input-box > *, "
    ".CardItem.current .input-box > *, "
    ".CardItem.showing .input-box > *, "
    ".input-box > *"
)


IGNORE_TEXTS = {
    "학습중...",
    "키보드로 숫자를 선택할 수 있습니다.",
    "알아요",
    "몰라요",
}


def norm_text(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def comparable_text(text):
    text = norm_text(text)
    text = re.sub(r"^\d+\s*[.)]\s*", "", text)
    text = re.sub(r"\[[^\]]+\]", "", text)
    return norm_text(text)


def click_first_available(driver, selectors, timeout=12):
    wait = WebDriverWait(driver, timeout)
    last_error = None
    for by, selector in selectors:
        try:
            element = wait.until(EC.element_to_be_clickable((by, selector)))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            driver.execute_script("arguments[0].click();", element)
            return element
        except TimeoutException as error:
            last_error = error
    raise last_error


def enter_recall(driver, timeout=15):
    end_time = time.time() + timeout
    while time.time() < end_time:
        if "/Recall/" in driver.current_url:
            return True
        for by, selector in ENTRY_SELECTORS:
            for element in driver.find_elements(by, selector):
                try:
                    if not element.is_displayed() or not element.is_enabled():
                        continue
                    target = " ".join(
                        [
                            element.get_attribute("href") or "",
                            element.get_attribute("onclick") or "",
                            element.get_attribute("data-href") or "",
                        ]
                    )
                    if "/Recall/" not in target and "리콜" not in element.text:
                        continue
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});",
                        element,
                    )
                    try:
                        element.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", element)
                    WebDriverWait(driver, 5).until(
                        lambda d: "/Recall/" in d.current_url
                    )
                    return True
                except Exception:
                    continue
        time.sleep(0.25)
    return False


def start_recall(driver, timeout=18):
    end_time = time.time() + timeout
    while time.time() < end_time:
        if sentence_recall_ready(driver):
            return "sentence"
        if get_active_card(driver) is not None:
            return "card"
        if recall_complete(driver):
            reset_buttons = [
                element
                for element in driver.find_elements(By.CSS_SELECTOR, ".btn-reset-section")
                if element.is_displayed() and element.is_enabled()
            ]
            if reset_buttons:
                driver.execute_script("arguments[0].click();", reset_buttons[0])
                try:
                    confirm = WebDriverWait(driver, 4).until(
                        lambda d: next(
                            (
                                element
                                for element in d.find_elements(
                                    By.CSS_SELECTOR, ".btn-ok.btn-danger"
                                )
                                if element.is_displayed() and element.is_enabled()
                            ),
                            None,
                        )
                    )
                    driver.execute_script("arguments[0].click();", confirm)
                except TimeoutException:
                    pass
                time.sleep(0.5)
                continue
        try:
            click_first_available(driver, START_BUTTON_SELECTORS, timeout=1)
        except TimeoutException:
            pass
        time.sleep(0.5)
    if sentence_recall_ready(driver):
        return "sentence"
    if get_active_card(driver) is not None:
        return "card"
    return ""


def visible_texts(driver, selector):
    return driver.execute_script(
        """
        return Array.from(document.querySelectorAll(arguments[0]))
            .filter(el => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && rect.width > 0
                    && rect.height > 0;
            })
            .map(el => el.innerText || el.textContent || '')
            .filter(Boolean);
        """,
        selector,
    )


def get_active_card(driver):
    selectors = [
        ".CardItem.current.showing:not(.deactive)",
        ".CardItem.current:not(.deactive)",
        ".CardItem.showing:not(.deactive):not(.previous):not(.next)",
    ]
    for selector in selectors:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                if element.is_displayed() and element.rect["width"] > 0:
                    return element
            except Exception:
                continue
    return None


def visible_element(driver, selector):
    for element in driver.find_elements(By.CSS_SELECTOR, selector):
        try:
            if element.is_displayed() and element.is_enabled():
                return element
        except Exception:
            continue
    return None


def sentence_recall_ready(driver):
    choice = visible_element(driver, SENTENCE_CHOICE_SELECTOR)
    return choice is not None


def normalize_sentence_token(value):
    value = re.sub(r"^[^\w']+|[^\w']+$", "", str(value or ""), flags=re.UNICODE)
    return value.casefold()


def sentence_recall_progress(driver):
    known = visible_element(driver, ".known_count")
    total = visible_element(driver, ".total_count")
    if known is None or total is None:
        return None
    try:
        return int(known.text.strip()), int(total.text.strip())
    except (TypeError, ValueError):
        return None


def sentence_recall_signature(driver):
    placed = [
        element.text.strip()
        for element in driver.find_elements(By.CSS_SELECTOR, SENTENCE_PLACED_SELECTOR)
        if element.is_displayed()
    ]
    choices = [
        element.text.strip()
        for element in driver.find_elements(By.CSS_SELECTOR, SENTENCE_CHOICE_SELECTOR)
        if element.is_displayed()
    ]
    return tuple(placed), tuple(choices)


def current_sentence_answer(driver, da_e):
    placed = [
        element.text.strip()
        for element in driver.find_elements(By.CSS_SELECTOR, SENTENCE_PLACED_SELECTOR)
        if element.is_displayed() and element.text.strip() != "?"
    ]
    choices = [
        element.text.strip()
        for element in driver.find_elements(By.CSS_SELECTOR, SENTENCE_CHOICE_SELECTOR)
        if element.is_displayed()
    ]
    placed_normalized = [
        normalize_sentence_token(token)
        for token in placed
        if normalize_sentence_token(token)
    ]
    choices_normalized = [
        normalize_sentence_token(token)
        for token in choices
        if normalize_sentence_token(token)
    ]

    for english in da_e:
        answer = norm_text(english)
        if not answer:
            continue
        raw_tokens = [
            token for token in answer.split() if normalize_sentence_token(token)
        ]
        answer_tokens = [
            normalize_sentence_token(token)
            for token in raw_tokens
        ]

        for start in range(len(answer_tokens) - len(placed_normalized) + 1):
            placed_end = start + len(placed_normalized)
            if answer_tokens[start:placed_end] != placed_normalized:
                continue
            choice_end = placed_end + len(choices_normalized)
            remaining_normalized = answer_tokens[placed_end:choice_end]
            if Counter(remaining_normalized) != Counter(choices_normalized):
                continue
            segment = raw_tokens[start:choice_end]
            remaining = raw_tokens[placed_end:choice_end]
            return " ".join(segment), remaining

    return "", []


def run_sentence_recall(driver, card_count, da_e):
    progress = sentence_recall_progress(driver)
    completed = progress[0] if progress else 0

    while completed < card_count:
        before_count = completed
        solved_segments = []

        while completed == before_count:
            if len(solved_segments) >= 30:
                raise RuntimeError("문장 리콜 한 카드의 구간 수가 30개를 넘었습니다.")
            answer, remaining = current_sentence_answer(driver, da_e)
            if not answer or not remaining:
                visible = driver.execute_script(
                    "return (document.body.innerText || '').slice(-1200);"
                )
                raise RuntimeError(
                    f"문장 리콜의 다음 구간을 찾지 못했습니다. 현재 화면: {visible}"
                )

            previous_signature = sentence_recall_signature(driver)
            used_ids = set()
            for expected_token in remaining:
                expected = normalize_sentence_token(expected_token)
                choice = None
                for element in driver.find_elements(
                    By.CSS_SELECTOR, SENTENCE_CHOICE_SELECTOR,
                ):
                    try:
                        if (
                            element.id not in used_ids
                            and element.is_displayed()
                            and normalize_sentence_token(element.text) == expected
                        ):
                            choice = element
                            break
                    except Exception:
                        continue
                if choice is None:
                    raise RuntimeError(
                        f"문장 리콜 조각 {expected_token!r}을 찾지 못했습니다."
                    )
                used_ids.add(choice.id)
                driver.execute_script("arguments[0].click();", choice)
                time.sleep(0.08)

            solved_segments.append(" ".join(remaining))
            print(
                f"문장 리콜 구간 처리: {' '.join(remaining)}",
                flush=True,
            )
            try:
                WebDriverWait(driver, 12).until(
                    lambda d: (
                        (
                            sentence_recall_progress(d) is not None
                            and sentence_recall_progress(d)[0] > before_count
                        )
                        or visible_element(d, ".btn-next-card") is not None
                        or (
                            sentence_recall_ready(d)
                            and sentence_recall_signature(d) != previous_signature
                        )
                    )
                )
            except TimeoutException as error:
                visible = driver.execute_script(
                    "return (document.body.innerText || '').slice(-1200);"
                )
                raise RuntimeError(
                    f"문장 리콜 다음 구간 전환을 기다리다 멈췄습니다: {visible}"
                ) from error

            progress = sentence_recall_progress(driver)
            if progress is not None and progress[0] > before_count:
                completed = progress[0]
                break

        print(
            f"문장 리콜 진행: {completed}/{card_count} - "
            f"{' / '.join(solved_segments)}"
        )

        next_button = visible_element(driver, ".btn-next-card")
        if next_button is None:
            raise RuntimeError("문장 리콜의 다음카드 버튼을 찾지 못했습니다.")
        driver.execute_script("arguments[0].click();", next_button)
        if completed < card_count:
            WebDriverWait(driver, 6).until(sentence_recall_ready)
        time.sleep(0.2)

    print(f"문장 리콜학습 처리 완료: {completed}/{card_count}")
    return completed


def recall_complete(driver):
    text = driver.execute_script(
        "return document.body.innerText || '';"
    )
    percentages = [int(value) for value in re.findall(r"(\d+)\s*%", text)]
    reached_target = any(value >= 200 for value in percentages)
    return (
        ("리콜 학습이 완료" in text)
        or ("구간 학습이 완료" in text)
        or ("리콜" in text and "완료" in text and reached_target)
        or ("Clear" in text and reached_target)
    )


def get_current_question(driver, da_e, da_k):
    known_terms = [norm_text(value) for value in [*da_e, *da_k] if norm_text(value) and value != 0]
    blocks = []

    try:
        active_card = get_active_card(driver)
        if active_card is not None:
            active_text = active_card.text
            if active_text:
                blocks.append(active_text)
    except Exception:
        pass

    selectors = [
        ".CardItem.showing .card-top .text-normal",
        ".CardItem.showing .card-bottom .text-normal",
        ".CardItem.showing .card-top",
        ".CardItem.showing .card-bottom",
        "#wrapper-learn .CardItem .text-normal",
        "#wrapper-learn .CardItem .card-top",
        "#wrapper-learn .CardItem .card-bottom",
        "#wrapper-learn .question",
    ]
    for selector in selectors:
        blocks.extend(visible_texts(driver, selector))

    for block in blocks:
        lines = [norm_text(line) for line in block.splitlines() if norm_text(line)]
        for line in lines:
            if line in IGNORE_TEXTS:
                continue
            if line in known_terms:
                return line
        for term in sorted(known_terms, key=len, reverse=True):
            if term and term in norm_text(block):
                return term
    return ""


def get_answer(question, da_e, da_k):
    question = norm_text(question)
    for english, korean in zip(da_e, da_k):
        if norm_text(english) == question:
            return norm_text(korean)
        if norm_text(korean) == question:
            return norm_text(english)
    return ""


def get_choice_elements(driver):
    active_card = get_active_card(driver)
    if active_card is None:
        return []

    choice_class = driver.execute_script("return window.cheat_item_class || ''; ")
    selectors = []
    if choice_class:
        selectors.append(f".{choice_class}")
    selectors.extend([
        "[class*='radio']",
        ".cc-table.middle.fill-parent-w",
    ])

    seen = set()
    elements = []
    for selector in selectors:
        for element in active_card.find_elements(By.CSS_SELECTOR, selector):
            if element.id in seen:
                continue
            seen.add(element.id)
            text = norm_text(element.text)
            if text and element.is_displayed() and re.search(r"^\d+\s*[.)]", text):
                elements.append(element)
    return elements


def choice_matches(choice_text, answer):
    raw_choice = norm_text(choice_text)
    clean_choice = comparable_text(raw_choice)
    raw_answer = norm_text(answer)
    clean_answer = comparable_text(raw_answer)
    return (
        raw_choice == raw_answer
        or clean_choice == clean_answer
        or (raw_answer and raw_answer in raw_choice)
        or (clean_answer and clean_answer in clean_choice)
        or (clean_choice and clean_choice in clean_answer)
    )


def press_choice_number(driver, choice_text):
    match = re.match(r"^(\d+)\s*[.)]", norm_text(choice_text))
    if not match:
        return False
    number = match.group(1)
    driver.find_element(By.TAG_NAME, "body").send_keys(number)
    return True


def click_choice(driver, choice):
    driver.execute_script(
        """
        const el = arguments[0];
        el.scrollIntoView({block: 'center'});
        for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
            el.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
        }
        """,
        choice,
    )


def click_matching_choice(driver, answer):
    choices = get_choice_elements(driver)
    for choice in choices:
        if choice_matches(choice.text, answer):
            print(f"정답 선택: {norm_text(choice.text)}")
            try:
                choice.click()
            except Exception:
                try:
                    click_choice(driver, choice)
                except Exception:
                    if not press_choice_number(driver, choice.text):
                        return False
            return True
    return False


def wait_until_choice_accepted(driver, card_id, timeout=4):
    end_time = time.time() + timeout
    while time.time() < end_time:
        if recall_complete(driver):
            return True
        current_cards = driver.find_elements(By.CSS_SELECTOR, ".CardItem.current")
        if not current_cards:
            return True
        current = current_cards[0]
        try:
            current_id = current.get_attribute("data-idx")
            classes = current.get_attribute("class") or ""
            if current_id != card_id or "deactive" in classes:
                return True
        except Exception:
            return True
        time.sleep(0.15)
    return False


def advance_to_next_card(driver, timeout=5):
    end_time = time.time() + timeout
    while time.time() < end_time:
        selectors = [
            "#wrapper-learn .btnNextCard",
            "#wrapper-learn .btn-next-card",
            "#wrapper-learn [class*='next-card']",
        ]
        for selector in selectors:
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                try:
                    if element.is_displayed() and element.is_enabled():
                        driver.execute_script("arguments[0].click();", element)
                        return True
                except Exception:
                    continue
        current = driver.find_elements(By.CSS_SELECTOR, ".CardItem.current.deactive")
        if current:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.SPACE)
            return True
        time.sleep(0.15)
    return False


def end_learning(driver):
    selectors = [
        (By.ID, "btn_end"),
        (By.CSS_SELECTOR, ".btn-top-menu a"),
        (By.XPATH, "//*[contains(normalize-space(.), '학습종료')]"),
        (By.XPATH, "//*[contains(normalize-space(.), '나가기')]"),
        (By.XPATH, "//*[contains(normalize-space(.), '확인')]"),
    ]
    for by, selector in selectors:
        try:
            element = WebDriverWait(driver, 2).until(EC.element_to_be_clickable((by, selector)))
            driver.execute_script("arguments[0].click();", element)
            time.sleep(0.5)
        except TimeoutException:
            continue


class RecallLearning:
    def __init__(self, driver: webdriver.Chrome):
        self.driver = driver

    def run(self, num_d: int, word_d: list) -> None:
        driver = self.driver
        wait = WebDriverWait(driver, 15)
        da_e, da_k, _ = word_d
        card_count = max(1, num_d - 1)
        completed = 0

        if not enter_recall(driver):
            raise TimeoutException("리콜학습 페이지에 진입하지 못했습니다.")
        if recall_complete(driver):
            print("리콜학습이 이미 필수 200% 완료 상태입니다.")
            return 0
        learning_mode = start_recall(driver)
        if not learning_mode:
            state = driver.execute_script(
                "return (document.body.innerText || '').slice(-1200);"
            )
            raise TimeoutException(
                f"리콜학습 문제를 시작하지 못했습니다. 현재 화면: {state}"
            )
        if learning_mode == "sentence":
            return run_sentence_recall(driver, card_count, da_e)
        time.sleep(1)
        last_question = ""

        for _ in range(card_count):
            try:
                end_time = time.time() + 15
                question = ""
                while time.time() < end_time:
                    if recall_complete(driver):
                        break
                    candidate = get_current_question(driver, da_e, da_k)
                    if (
                        candidate
                        and candidate != last_question
                        and get_choice_elements(driver)
                    ):
                        question = candidate
                        break
                    time.sleep(0.2)
                if recall_complete(driver):
                    break
                answer = get_answer(question, da_e, da_k)
                print(f"문제: {question!r} / 정답: {answer!r}")
                if not question or not answer:
                    raise RuntimeError("현재 리콜 문제 또는 정답을 찾지 못했습니다.")
                active_card = get_active_card(driver)
                card_id = (
                    active_card.get_attribute("data-idx")
                    if active_card is not None
                    else ""
                )
                accepted = False
                for attempt in range(2):
                    if not click_matching_choice(driver, answer):
                        raise RuntimeError("리콜 정답 선택지를 찾지 못했습니다.")
                    if wait_until_choice_accepted(driver, card_id):
                        accepted = True
                        break
                    time.sleep(0.5)
                if not accepted:
                    raise RuntimeError("리콜 정답 입력이 화면에 적용되지 않았습니다.")
                completed += 1
                last_question = question
                time.sleep(0.45)
            except Exception as error:
                print(f"리콜 학습 중 오류가 발생했습니다: {error}")
                raise
        print(f"리콜학습 처리 완료: {completed}/{card_count}")
        return completed
