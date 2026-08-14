import re
import time
from collections import Counter

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait


ENTRY_SELECTORS = [
    (By.CSS_SELECTOR, "[href*='/Memorize/']"),
    (By.CSS_SELECTOR, "[onclick*='/Memorize/']"),
    (By.CSS_SELECTOR, "[data-href*='/Memorize/']"),
    (
        By.XPATH,
        "//*[normalize-space(.)='암기' or normalize-space(.)='암기학습']"
        "/ancestor-or-self::*[self::a or @onclick][1]",
    ),
]

START_SELECTORS = [
    (
        By.XPATH,
        "//*[self::a or self::button]"
        "[contains(normalize-space(.), '암기학습')"
        " and contains(normalize-space(.), '전체구간')]",
    ),
    (By.XPATH, "//*[normalize-space(.)='암기학습 (전체구간)']"),
    (By.CSS_SELECTOR, "#wrapper-learn .btn-opt-start"),
    (By.CSS_SELECTOR, "#wrapper-learn a.btn-success"),
    (By.CSS_SELECTOR, "#wrapper-learn .start-opt-body a"),
    (By.XPATH, "//*[@id='wrapper-learn']//*[normalize-space(.)='새로 시작']"),
    (By.XPATH, "//*[@id='wrapper-learn']//*[contains(normalize-space(.), '다시 학습')]"),
    (By.XPATH, "//*[@id='wrapper-learn']//*[contains(normalize-space(.), '이어하기')]"),
    (By.XPATH, "//*[self::a or self::button][contains(normalize-space(.), '% 도전')]"),
    (By.XPATH, "//*[self::a or self::button][contains(normalize-space(.), '%도전')]"),
    (By.XPATH, "//*[@id='wrapper-learn']//*[normalize-space(.)='시작']"),
]

REVEAL_SELECTORS = [
    ".card-cover",
    ".btn-down-cover-box",
    "[class*='down-cover']",
]

KNOWN_SELECTORS = [
    ".btn-know-box",
    ".btn_know",
    "[class*='know-box']",
]

GLOBAL_KNOWN_SELECTORS = [
    (By.CSS_SELECTOR, ".study-bottom .btn-know-box a"),
    (By.CSS_SELECTOR, ".study-bottom .btn-short-change-card"),
    (By.CSS_SELECTOR, ".study-bottom .btn-know-box"),
]


def click_visible(driver, selectors, timeout=8):
    end_time = time.time() + timeout
    while time.time() < end_time:
        for by, selector in selectors:
            for element in driver.find_elements(by, selector):
                try:
                    if not element.is_displayed() or not element.is_enabled():
                        continue
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});",
                        element,
                    )
                    try:
                        element.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", element)
                    return True
                except Exception:
                    continue
        time.sleep(0.2)
    return False


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


def sentence_practice_button(driver):
    button = visible_element(driver, ".btn-go-step1")
    if button is not None:
        return button
    return driver.execute_script(
        r"""
        const matches = Array.from(document.querySelectorAll('*'))
          .filter(el => (el.innerText || '').replace(/\s+/g, ' ').trim()
            .includes('영작 연습하기'))
          .sort((a, b) => {
            const ar = a.getBoundingClientRect(), br = b.getBoundingClientRect();
            return (br.width * br.height) - (ar.width * ar.height)
              || a.children.length - b.children.length;
          });
        return matches[0] || null;
        """
    )


def sentence_screen_ready(driver):
    start_button = visible_element(driver, ".btn-opt-start")
    practice_button = sentence_practice_button(driver)
    body = driver.execute_script("return document.body.innerText || '';")
    if "영작 연습하기" in body:
        return True
    return start_button is None and practice_button is not None


def restart_completed_section(driver):
    text = driver.execute_script("return document.body.innerText || '';")
    if "구간 학습이 완료" not in text and "새로 학습하기" not in text:
        return False
    reset = next(
        (
            element
            for element in driver.find_elements(By.CSS_SELECTOR, ".btn-reset-section")
            if element.is_displayed() and element.is_enabled()
        ),
        None,
    )
    if reset is None:
        return False
    driver.execute_script("arguments[0].click();", reset)
    try:
        confirm = WebDriverWait(driver, 4).until(
            lambda d: next(
                (
                    element
                    for element in d.find_elements(By.CSS_SELECTOR, ".btn-ok.btn-danger")
                    if element.is_displayed() and element.is_enabled()
                ),
                None,
            )
        )
        driver.execute_script("arguments[0].click();", confirm)
    except TimeoutException:
        pass
    time.sleep(0.5)
    return True


def enter_memorize(driver, timeout=15):
    original_url = driver.current_url
    end_time = time.time() + timeout

    while time.time() < end_time:
        if "/Memorize/" in driver.current_url:
            return True

        for by, selector in ENTRY_SELECTORS:
            for element in driver.find_elements(by, selector):
                try:
                    if not element.is_displayed() or not element.is_enabled():
                        continue

                    href = element.get_attribute("href") or ""
                    onclick = element.get_attribute("onclick") or ""
                    data_href = element.get_attribute("data-href") or ""
                    target_data = f"{href} {onclick} {data_href}"
                    if "/Memorize/" not in target_data and "암기" not in element.text:
                        continue

                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});",
                        element,
                    )
                    try:
                        element.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", element)

                    try:
                        WebDriverWait(driver, 5).until(
                            lambda d: "/Memorize/" in d.current_url
                        )
                        return True
                    except TimeoutException:
                        if driver.current_url != original_url:
                            driver.back()
                            WebDriverWait(driver, 8).until(
                                lambda d: d.current_url == original_url
                                or d.find_elements(By.CSS_SELECTOR, "div.flip-body")
                            )
                except Exception:
                    continue
        time.sleep(0.25)
    return False


def click_in_active_card(driver, selectors, timeout=8):
    end_time = time.time() + timeout
    while time.time() < end_time:
        card = get_active_card(driver)
        if card is not None:
            for selector in selectors:
                for element in card.find_elements(By.CSS_SELECTOR, selector):
                    try:
                        if not element.is_displayed() or not element.is_enabled():
                            continue
                        driver.execute_script(
                            "arguments[0].scrollIntoView({block: 'center'});",
                            element,
                        )
                        try:
                            element.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", element)
                        return True
                    except Exception:
                        continue
        time.sleep(0.15)
    return False


def get_progress(driver):
    text = driver.execute_script("return document.body.innerText || '';")
    matches = re.findall(r"(\d+)\s*\|\s*(\d+)", text)
    if not matches:
        return None
    current, total = matches[0]
    return int(current), int(total)


def known_button_visible(driver):
    for by, selector in GLOBAL_KNOWN_SELECTORS:
        for element in driver.find_elements(by, selector):
            try:
                if element.is_displayed() and element.is_enabled():
                    return True
            except Exception:
                continue
    return False


def start_until_learning(driver, timeout=18):
    end_time = time.time() + timeout
    while time.time() < end_time:
        if get_active_card(driver) is not None:
            return "card"
        if sentence_screen_ready(driver):
            return "sentence"
        if restart_completed_section(driver):
            continue
        click_visible(driver, START_SELECTORS, timeout=1)
        time.sleep(0.6)
    if get_active_card(driver) is not None:
        return "card"
    if sentence_screen_ready(driver):
        return "sentence"
    return ""


def normalize_token(value):
    value = re.sub(r"^[^\w']+|[^\w']+$", "", str(value or ""), flags=re.UNICODE)
    return value.casefold()


def sentence_answer(prompt, word_d):
    da_e, da_k, _ = word_d
    prompt = re.sub(r"\s+", " ", str(prompt or "")).strip()
    for english, korean in zip(da_e, da_k):
        normalized_english = re.sub(r"\s+", " ", str(english or "")).strip()
        normalized_korean = re.sub(r"\s+", " ", str(korean or "")).strip()
        if prompt in {normalized_english, normalized_korean}:
            return normalized_english
    return ""


def sentence_answer_from_screen(screen_text, word_d):
    normalized_screen = re.sub(r"\s+", " ", str(screen_text or "")).strip()
    da_e, da_k, _ = word_d
    matches = []
    for english, korean in zip(da_e, da_k):
        normalized_english = re.sub(r"\s+", " ", str(english or "")).strip()
        normalized_korean = re.sub(r"\s+", " ", str(korean or "")).strip()
        if not normalized_english:
            continue
        if (
            normalized_english in normalized_screen
            or (normalized_korean and normalized_korean in normalized_screen)
        ):
            matches.append(normalized_english)
    if matches:
        return max(matches, key=len)

    tutorial_candidates = []
    for line in str(screen_text or "").splitlines():
        normalized = re.sub(r"\s+", " ", line).strip()
        latin_words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ']+", normalized)
        if len(latin_words) >= 3 and re.search(r"[.!?][\"']?$", normalized):
            tutorial_candidates.append(normalized)
    return " ".join(tutorial_candidates)


def sentence_progress(driver):
    known = visible_element(driver, ".known_count")
    total = visible_element(driver, ".total_count")
    if known is None or total is None:
        return None
    try:
        return int(known.text.strip()), int(total.text.strip())
    except (TypeError, ValueError):
        return None


def sentence_memorize_signature(driver):
    return tuple(
        element.text.strip()
        for element in driver.find_elements(By.CSS_SELECTOR, ".scramble-item")
        if element.is_displayed()
    )


def sentence_memorize_segment(answer, choices, expected_start=0):
    raw_tokens = [
        token for token in answer.split() if normalize_token(token)
    ]
    normalized_tokens = [normalize_token(token) for token in raw_tokens]
    choice_tokens = [
        normalize_token(token) for token in choices if normalize_token(token)
    ]
    candidates = []
    for start in range(len(normalized_tokens) - len(choice_tokens) + 1):
        end = start + len(choice_tokens)
        if Counter(normalized_tokens[start:end]) != Counter(choice_tokens):
            continue
        candidates.append(
            (
                int(start == expected_start),
                -abs(start - expected_start),
                raw_tokens[start:end],
                end,
            )
        )
    if not candidates:
        return [], expected_start
    candidates.sort(key=lambda item: item[:2], reverse=True)
    return candidates[0][2], candidates[0][3]


def run_sentence_memorize(driver, card_count, word_d):
    completed = sentence_progress(driver)
    completed = completed[0] if completed else 0

    while completed < card_count:
        progress_before = sentence_progress(driver)
        is_tutorial = progress_before is not None and progress_before[1] == 0
        before_text = driver.execute_script("return document.body.innerText || '';")
        answer_before = sentence_answer_from_screen(before_text, word_d)
        practice_button = sentence_practice_button(driver)
        if practice_button is None:
            ActionChains(driver).send_keys(Keys.SPACE).perform()
        else:
            driver.execute_script("arguments[0].click();", practice_button)

        WebDriverWait(driver, 6).until(
            lambda d: visible_element(d, ".scramble-item") is not None
        )
        prompt_element = visible_element(driver, ".para_item.active")
        prompt = prompt_element.text.strip() if prompt_element is not None else ""
        answer = sentence_answer(prompt, word_d) or answer_before
        if not answer:
            raise RuntimeError(
                f"문장 암기 정답을 찾지 못했습니다: 문제={prompt!r}"
            )

        before_count = completed
        if is_tutorial:
            for desired in answer.split():
                expected = normalize_token(desired)
                choice = next(
                    (
                        element
                        for element in driver.find_elements(
                            By.CSS_SELECTOR, ".scramble-item"
                        )
                        if element.is_displayed()
                        and normalize_token(element.text) == expected
                    ),
                    None,
                )
                if choice is None:
                    raise RuntimeError(
                        f"문장 암기 튜토리얼 조각 {desired!r}을 찾지 못했습니다."
                    )
                driver.execute_script("arguments[0].click();", choice)
                time.sleep(0.06)
            WebDriverWait(driver, 8).until(
                lambda d: visible_element(d, ".btn-next-card") is not None
            )
            print("문장 암기 튜토리얼 처리 완료")
            next_button = visible_element(driver, ".btn-next-card")
            driver.execute_script("arguments[0].click();", next_button)
            WebDriverWait(driver, 6).until(
                lambda d: sentence_practice_button(d) is not None
            )
            time.sleep(0.2)
            continue

        expected_start = 0
        solved_segments = []
        while True:
            if len(solved_segments) >= 30:
                raise RuntimeError("문장 암기 한 카드의 구간 수가 30개를 넘었습니다.")
            choices = list(sentence_memorize_signature(driver))
            segment, segment_end = sentence_memorize_segment(
                answer, choices, expected_start
            )
            if not segment:
                raise RuntimeError(
                    f"문장 암기 조각을 원문과 맞추지 못했습니다: "
                    f"원문={answer!r}, 조각={choices}"
                )

            previous_signature = tuple(choices)
            used_ids = set()
            for desired in segment:
                expected = normalize_token(desired)
                choice = None
                for element in driver.find_elements(By.CSS_SELECTOR, ".scramble-item"):
                    try:
                        if (
                            element.id not in used_ids
                            and element.is_displayed()
                            and normalize_token(element.text) == expected
                        ):
                            choice = element
                            break
                    except Exception:
                        continue
                if choice is None:
                    raise RuntimeError(f"문장 암기 조각 {desired!r}을 찾지 못했습니다.")
                used_ids.add(choice.id)
                driver.execute_script("arguments[0].click();", choice)
                time.sleep(0.06)

            expected_start = segment_end
            solved_segments.append(" ".join(segment))
            WebDriverWait(driver, 12).until(
                lambda d: (
                    visible_element(d, ".btn-next-card") is not None
                    or (
                        sentence_progress(d) is not None
                        and sentence_progress(d)[0] > before_count
                    )
                    or sentence_memorize_signature(d) != previous_signature
                )
            )
            progress_now = sentence_progress(driver)
            if progress_now is not None and progress_now[0] > before_count:
                completed = progress_now[0]
                break

        print(
            f"문장 암기 진행: {completed}/{card_count} - "
            f"{' / '.join(solved_segments)}"
        )

        next_button = visible_element(driver, ".btn-next-card")
        driver.execute_script("arguments[0].click();", next_button)
        if completed < card_count:
            WebDriverWait(driver, 6).until(
                lambda d: sentence_practice_button(d) is not None
            )
        time.sleep(0.2)

    print(f"문장 암기학습 처리 완료: {completed}/{card_count}")
    return completed


def learning_finished(driver):
    text = driver.execute_script("return document.body.innerText || '';")
    return any(
        marker in text
        for marker in ("암기 학습이 완료", "학습 완료", "수고하셨습니다", "Clear")
    )


def finish_learning(driver):
    selectors = [
        (By.XPATH, "//*[normalize-space(.)='학습종료']"),
        (By.ID, "btn_end"),
        (By.CSS_SELECTOR, ".study-header-body a"),
    ]
    if not click_visible(driver, selectors, timeout=5):
        return False
    time.sleep(0.5)
    confirm_selectors = [
        (By.CSS_SELECTOR, "#confirmModal .btn-ok"),
        (By.CSS_SELECTOR, "#alertModal .btn-ok"),
        (
            By.XPATH,
            "//*[contains(@class, 'modal') and contains(@class, 'in')]"
            "//*[normalize-space(.)='확인' or contains(normalize-space(.), '종료')]",
        ),
    ]
    click_visible(driver, confirm_selectors, timeout=2)
    time.sleep(1.5)
    return True


class RoteLearning:
    def __init__(self, driver: webdriver.Chrome):
        self.driver = driver

    def run(self, num_d: int, word_d: list = None) -> int:
        driver = self.driver
        card_count = max(1, num_d - 1)

        if not enter_memorize(driver):
            state = driver.execute_script(
                """
                return {
                    url: location.href,
                    memorizeLinks: Array.from(document.querySelectorAll(
                        "[href*='/Memorize/'], [onclick*='/Memorize/']"
                    )).map(el => ({
                        text: (el.innerText || '').trim(),
                        href: el.getAttribute('href') || '',
                        onclick: el.getAttribute('onclick') || '',
                        visible: !!(el.offsetWidth || el.offsetHeight)
                    })),
                    screen: (document.body.innerText || '').slice(-1200)
                };
                """
            )
            raise TimeoutException(f"암기학습에 진입하지 못했습니다: {state}")
        learning_mode = start_until_learning(driver)
        if not learning_mode:
            state = driver.execute_script(
                "return (document.body.innerText || '').slice(-1200);"
            )
            raise TimeoutException(
                f"암기학습 카드를 시작하지 못했습니다. 현재 화면: {state}"
            )
        if learning_mode == "sentence":
            return run_sentence_memorize(driver, card_count, word_d)
        completed = get_progress(driver)
        completed = completed[0] if completed else 0

        while completed < card_count:
            if learning_finished(driver):
                break

            before = get_progress(driver)
            before_count = before[0] if before else completed

            try:
                ActionChains(driver).send_keys(Keys.SPACE).perform()
                WebDriverWait(driver, 3).until(known_button_visible)
                revealed = True
            except Exception:
                revealed = click_in_active_card(driver, REVEAL_SELECTORS, timeout=4)
                if revealed:
                    try:
                        WebDriverWait(driver, 3).until(known_button_visible)
                    except TimeoutException:
                        revealed = False
            if not revealed:
                state = driver.execute_script(
                    """
                    return Array.from(document.querySelectorAll(
                        '[class*="cover"], [class*="know"], a, button'
                    ))
                    .filter(el => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none' && s.visibility !== 'hidden';
                    })
                    .map(el => ({
                        tag: el.tagName,
                        className: el.className,
                        text: (el.innerText || '').trim(),
                        parent: el.parentElement?.className || ''
                    }))
                    .filter(item =>
                        /cover|know|알아|의미|SPACE/i.test(
                            item.className + ' ' + item.text + ' ' + item.parent
                        )
                    );
                    """
                )
                raise RuntimeError(f"현재 암기 카드의 커버 버튼을 찾지 못했습니다: {state}")
            advanced = False
            for _ in range(2):
                try:
                    if not click_visible(driver, GLOBAL_KNOWN_SELECTORS, timeout=3):
                        continue
                    WebDriverWait(driver, 4).until(
                        lambda d: learning_finished(d)
                        or (
                            get_progress(d) is not None
                            and get_progress(d)[0] > before_count
                        )
                    )
                    advanced = True
                    break
                except TimeoutException:
                    time.sleep(0.25)

            if not advanced:
                progress = get_progress(driver)
                state = driver.execute_script(
                    """
                    const card = document.querySelector(
                        '.CardItem.current.showing:not(.deactive),'
                        + '.CardItem.current:not(.deactive)'
                    );
                    return {
                        progress: document.body.innerText.match(/\\d+\\s*\\|\\s*\\d+/)?.[0] || '',
                        cardClass: card?.className || '',
                        cardText: (card?.innerText || '').slice(-500),
                        cardHtml: (card?.outerHTML || '').slice(-5000),
                        buttons: card
                            ? Array.from(card.querySelectorAll('a, button'))
                                .map(el => ({className: el.className, text: el.innerText}))
                            : []
                    };
                    """
                )
                raise RuntimeError(f"암기 카드가 다음으로 넘어가지 않았습니다: {state}")

            progress = get_progress(driver)
            completed = progress[0] if progress else before_count + 1
            print(f"암기 진행: {completed}/{card_count}")
            time.sleep(0.25)

        print(f"암기학습 처리 완료: {completed}/{card_count}")
        if completed < card_count and not learning_finished(driver):
            raise RuntimeError(f"암기학습이 {completed}/{card_count}에서 중단되었습니다.")
        if not learning_finished(driver) and not finish_learning(driver):
            raise RuntimeError("암기학습 종료 버튼을 찾지 못했습니다.")
        return completed
