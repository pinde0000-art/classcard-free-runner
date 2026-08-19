import json
import os
import time
import traceback
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import UnexpectedAlertPresentException
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from handler.recall_learning import RecallLearning
from handler.rote_learning import RoteLearning
from handler.spelling_learning import SpellingLearning
from handler.test_learning import TestLearning
from utility import get_account, word_get


TARGETS = [
    ("능률VOCA 중등 필수 [2025] DAY 16", "25381021", "2046211"),
    ("능률VOCA 중등 필수 [2025] DAY 17", "25381022", "2046211"),
    ("능률VOCA 중등 필수 [2025] DAY 18", "25381023", "2046211"),
    ("능률VOCA 중등 필수 [2025] DAY 19", "25381024", "2046211"),
    ("Reading Inside Starter [2022] - U12", "10159070", "2059431"),
    ("Reading Inside Starter [2022] - U12 Reading 1", "10513896", "2059431"),
    ("Reading Inside Starter [2022] - U12 Reading 2", "10513897", "2059431"),
]

MODES = [
    ("암기", RoteLearning),
    ("리콜", RecallLearning),
    ("스펠", SpellingLearning),
    ("테스트", TestLearning),
]

REPEAT_COUNT = 2
SENTENCE_TEST_SET_IDS = {"10513896", "10513897"}
ROOT = Path(__file__).resolve().parent
CHECKPOINT_PATH = ROOT / "batch_checkpoint.json"
LOG_PATH = ROOT / "batch_learning.log"
DEBUG_DIR = ROOT / "batch_debug"


def log(message):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(line + "\n")


def load_checkpoint():
    if not CHECKPOINT_PATH.exists():
        return {"completed": {}, "snapshots": {}}
    try:
        return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"completed": {}, "snapshots": {}}


def save_checkpoint(checkpoint):
    CHECKPOINT_PATH.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def create_login_session(account=None, user_agent=None):
    account = account or get_account()
    session = requests.Session()
    if user_agent:
        session.headers.update({"User-Agent": user_agent})
    session.get("https://www.classcard.net/Login", timeout=20)
    response = session.post(
        "https://www.classcard.net/LoginProc",
        data={"login_id": account["id"], "login_pwd": account["pw"]},
        headers={
            "Referer": "https://www.classcard.net/Login",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=20,
    )
    if response.json().get("result") != "ok":
        raise RuntimeError("클래스카드 HTTP 로그인에 실패했습니다.")
    return session


def install_session_cookies(driver, session):
    driver.execute_cdp_cmd("Network.enable", {})
    for cookie in session.cookies:
        params = {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain or "www.classcard.net",
            "path": cookie.path or "/",
            "secure": bool(cookie.secure),
        }
        result = driver.execute_cdp_cmd("Network.setCookie", params)
        if not result.get("success", False):
            raise RuntimeError(f"로그인 쿠키를 설정하지 못했습니다: {cookie.name}")


def login(driver, authenticated_session=None):
    account = get_account()

    # Cloud Chrome can miss the site's client-side login redirect. Create the
    # authenticated session over HTTP and transfer its cookies to Chrome first.
    try:
        session = authenticated_session or create_login_session(
            account,
            driver.execute_script("return navigator.userAgent"),
        )
        install_session_cookies(driver, session)
        return
    except Exception as error:
        log(f"HTTP login fallback failed: {type(error).__name__}: {error}")

    for attempt in range(1, 4):
        wait = WebDriverWait(driver, 20)
        try:
            driver.get("https://www.classcard.net/Login")
            login_id = wait.until(
                EC.visibility_of_element_located((By.NAME, "login_id"))
            )
            login_pw = wait.until(
                EC.visibility_of_element_located((By.NAME, "login_pwd"))
            )
            login_id.clear()
            login_pw.clear()
            login_id.send_keys(account["id"])
            login_pw.send_keys(account["pw"])
            wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn-login"))
            ).click()
            wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".left-class-list"))
            )
            return
        except UnexpectedAlertPresentException:
            try:
                alert = driver.switch_to.alert
                log(f"로그인 경고창 처리: {alert.text!r} ({attempt}/3)")
                alert.accept()
            except Exception:
                pass
            time.sleep(attempt * 3)
    raise RuntimeError("클래스카드 로그인에 세 번 실패했습니다.")


def open_set(driver, set_id, class_id):
    url = f"https://www.classcard.net/set/{set_id}/{class_id}"
    driver.get(url)
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.flip-body"))
    )
    time.sleep(0.15)
    try:
        dropdown = driver.find_element(
            By.CSS_SELECTOR,
            "div.set-body div.dropdown > a",
        )
        driver.execute_script("arguments[0].click();", dropdown)
        first_option = WebDriverWait(driver, 3).until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "div.set-body div.dropdown.open ul > li:first-child > a")
            )
        )
        driver.execute_script("arguments[0].click();", first_option)
        time.sleep(0.1)
    except Exception:
        pass
    return url


def load_words(driver):
    cards = driver.find_elements(
        By.XPATH,
        "//*[@id='tab_set_all']/div[2]/div"
        "[div[4]/div[1]/div[1]/div/div and div[4]/div[2]/div[1]/div/div]",
    )
    if not cards:
        raise RuntimeError("세트 카드 목록을 찾지 못했습니다.")
    num_d = len(cards) + 1
    return num_d, word_get(driver, num_d)


def snapshot_set(driver):
    text = driver.execute_script(
        """
        return Array.from(document.querySelectorAll('body *'))
            .filter(el => {
                const t = (el.innerText || '').trim();
                const style = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return t && (t.includes('%') || t.includes('완료'))
                    && style.display !== 'none' && style.visibility !== 'hidden'
                    && r.width > 0 && r.height > 0 && el.children.length < 8;
            })
            .map(el => (el.innerText || '').trim())
            .filter((v, i, a) => a.indexOf(v) === i)
            .slice(0, 30);
        """
    )
    return text


def read_learning_progress(driver):
    return driver.execute_script(
        """
        const result = {암기: 0, 리콜: 0, 스펠: 0};
        const items = document.querySelectorAll(
            '.bottom-fixed .dp-inline-block, .bottom-fixed .btn-summary'
        );
        for (const item of items) {
            const text = (item.innerText || '').trim();
            const mode = ['암기', '리콜', '스펠'].find(name => text.includes(name));
            if (!mode) continue;
            const match = text.match(/(\\d+)\\s*%/);
            if (match) result[mode] = Math.max(result[mode], Number(match[1]));
        }
        return result;
        """
    )


def save_debug(driver, key):
    DEBUG_DIR.mkdir(exist_ok=True)
    safe_key = "".join(char if char.isalnum() else "_" for char in key)
    driver.save_screenshot(str(DEBUG_DIR / f"{safe_key}.png"))
    (DEBUG_DIR / f"{safe_key}.html").write_text(
        driver.page_source,
        encoding="utf-8",
    )


def main():
    checkpoint = load_checkpoint()
    options = Options()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--log-level=1")
    driver = webdriver.Chrome(options=options)

    try:
        login(driver)
        log("로그인 완료")

        for title, set_id, class_id in TARGETS:
            log(f"세트 시작: {title}")
            set_url = open_set(driver, set_id, class_id)
            num_d, word_d = load_words(driver)
            log(f"카드 {num_d - 1}개 확인")

            selected_modes = MODES[:3]
            if set_id in SENTENCE_TEST_SET_IDS:
                selected_modes = MODES

            for mode_name, handler_class in selected_modes:
                if mode_name != "테스트":
                    attempts = 0
                    while True:
                        open_set(driver, set_id, class_id)
                        before_progress = read_learning_progress(driver).get(mode_name, 0)
                        log(f"서버 상태: {title} / {mode_name} {before_progress}%")
                        if before_progress >= 200:
                            log(
                                f"목표 도달로 건너뜀: {title} / "
                                f"{mode_name} {before_progress}%"
                            )
                            break
                        if attempts >= 2:
                            raise RuntimeError(
                                f"{title} / {mode_name}가 두 번 실행 후에도 "
                                f"{before_progress}%입니다."
                            )

                        log(f"실행: {title} / {mode_name} / 현재 {before_progress}%")
                        handler = handler_class(driver)
                        completed = handler.run(num_d=num_d, word_d=word_d)
                        time.sleep(3)
                        open_set(driver, set_id, class_id)
                        after_progress = read_learning_progress(driver).get(mode_name, 0)
                        log(
                            f"서버 확인: {title} / {mode_name} "
                            f"{before_progress}% -> {after_progress}%"
                        )
                        if after_progress <= before_progress:
                            raise RuntimeError(
                                f"{title} / {mode_name} 완료 기록이 서버에 반영되지 않았습니다."
                            )
                        attempts += 1
                    continue

                for round_number in range(1, REPEAT_COUNT + 1):
                    key = f"{set_id}:{mode_name}:{round_number}"
                    if checkpoint["completed"].get(key):
                        log(f"이미 완료되어 건너뜀: {title} / {mode_name} {round_number}회")
                        continue

                    open_set(driver, set_id, class_id)
                    log(f"실행: {title} / {mode_name} {round_number}/{REPEAT_COUNT}")
                    try:
                        handler = handler_class(driver)
                        completed = handler.run(num_d=num_d, word_d=word_d)
                        checkpoint["completed"][key] = {
                            "title": title,
                            "mode": mode_name,
                            "round": round_number,
                            "items": completed,
                            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        }
                        save_checkpoint(checkpoint)
                        log(
                            f"완료: {title} / {mode_name} "
                            f"{round_number}/{REPEAT_COUNT} / {completed}개"
                        )
                    except Exception as error:
                        log(f"오류: {title} / {mode_name} {round_number}회 / {error}")
                        log(traceback.format_exc())
                        save_debug(driver, key)
                        raise

            driver.get(set_url)
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.flip-body"))
            )
            snapshot = snapshot_set(driver)
            checkpoint["snapshots"][set_id] = {
                "title": title,
                "texts": snapshot,
                "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            save_checkpoint(checkpoint)
            log(f"상태 확인: {title} / {snapshot}")

        log("전체 대상 학습 완료")
    finally:
        driver.quit()
        log("브라우저 종료")


if __name__ == "__main__":
    main()
