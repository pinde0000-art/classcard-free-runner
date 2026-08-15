import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from batch_learning import login, open_set
from utility import DATA_DIR, get_account


ROOT = Path(__file__).resolve().parent
CATALOG_CACHE = DATA_DIR / "classcard_catalog_cache.json"


def make_driver():
    options = Options()
    options.page_load_strategy = "eager"
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1280,1000")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--mute-audio")
    options.add_experimental_option(
        "prefs",
        {
            "profile.managed_default_content_settings.images": 2,
            "profile.default_content_setting_values.notifications": 2,
        },
    )
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--log-level=1")
    driver = webdriver.Chrome(options=options)
    # Selenium의 기본 원격 연결에는 클라이언트 쪽 타임아웃이 없다(소켓 기본값
    # 그대로면 무한 대기). 브라우저 탭이나 chromedriver가 내부적으로 멈추면
    # execute_script/execute_cdp_cmd 같은 단일 명령이 응답을 영영 못 받고
    # 블로킹돼, 우리 쪽 WebDriverWait 타임아웃/재시도 로직이 전혀 소용없어진다
    # (실제로 GitHub Actions에서 2시간 워크플로 제한에 걸릴 때까지 조용히
    # 멈춘 사례가 있었다). 정상적인 단일 명령이 30초 넘게 걸릴 이유가 없으므로
    # 명령 단위 타임아웃을 걸어, 멈추면 예외로 드러나게 한다.
    driver.command_executor._client_config.timeout = 30
    return driver


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def read_class_links(driver):
    """현재 화면 전체에서 클래스 메인 링크를 읽는다."""
    classes = []
    seen = set()
    for anchor in driver.find_elements(By.CSS_SELECTOR, "a[href]"):
        href = anchor.get_attribute("href") or ""
        match = re.search(r"/ClassMain/(\d+)(?:[/?#]|$)", href, re.IGNORECASE)
        if not match:
            continue
        class_id = match.group(1)
        if class_id in seen:
            continue
        seen.add(class_id)
        name = clean_text(
            anchor.get_attribute("innerText")
            or anchor.get_attribute("textContent")
            or anchor.text
        )
        classes.append({"class_id": class_id, "class_name": name or f"클래스 {class_id}"})
    return classes


def discover_catalog():
    driver = make_driver()
    try:
        login(driver)
        # HTTP 로그인 경로는 인증 쿠키만 Chrome에 설치한다. 로그인 직후의
        # 빈 탭에서 클래스 목록을 기다리지 말고 인증된 홈을 먼저 연다.
        driver.get("https://www.classcard.net/")
        try:
            classes = WebDriverWait(driver, 20).until(
                lambda current: read_class_links(current) or False
            )
        except Exception as error:
            raise RuntimeError(
                f"클래스 링크를 찾지 못했습니다 (화면: {driver.current_url})."
            ) from error

        cookies = {cookie["name"]: cookie["value"] for cookie in driver.get_cookies()}
        user_agent = driver.execute_script("return navigator.userAgent")

        def fetch_sets(item):
            response = requests.get(
                f"https://www.classcard.net/ClassMain/{item['class_id']}",
                cookies=cookies,
                headers={"User-Agent": user_agent},
                timeout=20,
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            sets = []
            set_seen = set()
            for row in soup.select(".set-items"):
                try:
                    anchor = row.select_one("a[data-idx]")
                    set_id = str(anchor.get("data-idx") or "")
                    if not set_id.isdigit() or set_id in set_seen:
                        continue
                    set_seen.add(set_id)
                    title = clean_text(anchor.get_text(" ", strip=True))
                    count = ""
                    for span in anchor.select("span"):
                        candidate = clean_text(span.get_text(" ", strip=True))
                        if re.search(r"\d+", candidate):
                            count = candidate
                            title = clean_text(title.replace(candidate, ""))
                    sets.append({"set_id": set_id, "title": title or f"세트 {set_id}", "count": count})
                except Exception:
                    continue
            return {**item, "sets": sets}

        with ThreadPoolExecutor(max_workers=min(6, max(1, len(classes)))) as executor:
            result = list(executor.map(fetch_sets, classes))
        return result
    finally:
        driver.quit()


def get_catalog(max_age=300):
    account_id = str(get_account()["id"])
    try:
        cached = json.loads(CATALOG_CACHE.read_text(encoding="utf-8"))
        if (
            cached.get("account_id") == account_id
            and time.time() - float(cached["saved_at"]) <= max_age
        ):
            catalog = cached["catalog"]
            if catalog:
                return catalog
    except Exception:
        pass

    catalog = discover_catalog()
    try:
        CATALOG_CACHE.write_text(
            json.dumps(
                {
                    "saved_at": time.time(),
                    "account_id": account_id,
                    "catalog": catalog,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    return catalog


def read_cards(driver):
    cards = driver.find_elements(By.CSS_SELECTOR, "#tab_set_all .flip-card[data-idx]")
    if not cards:
        cards = driver.find_elements(By.CSS_SELECTOR, ".flip-card[data-idx]")
    result = []
    for number, card in enumerate(cards, start=1):
        def card_text(selector):
            try:
                element = card.find_element(By.CSS_SELECTOR, selector)
                return clean_text(
                    element.get_attribute("innerText")
                    or element.get_attribute("textContent")
                    or element.text
                )
            except Exception:
                return ""

        icon_class = ""
        try:
            icon_class = card.find_element(By.CSS_SELECTOR, ".btn-favor i").get_attribute("class") or ""
        except Exception:
            pass
        result.append(
            {
                "number": number,
                "card_id": str(card.get_attribute("data-idx") or ""),
                "front": card_text(".ex_front"),
                "back": card_text(".ex_back"),
                "example": card_text(".ex_example"),
                "favorite": "star_o" not in icon_class and "star" in icon_class,
            }
        )
    if not result:
        raise RuntimeError("이 세트의 카드 목록을 찾지 못했습니다.")
    return result


def load_set_cards(class_id, set_id):
    if not str(class_id).isdigit() or not str(set_id).isdigit():
        raise ValueError("클래스 또는 세트 번호가 올바르지 않습니다.")
    driver = make_driver()
    try:
        login(driver)
        open_set(driver, str(set_id), str(class_id))
        return read_cards(driver)
    finally:
        driver.quit()
