import argparse
import base64
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from batch_learning import create_login_session, login, open_set, read_learning_progress
from classcard_catalog import make_driver, read_cards
from handler.recall_learning import RecallLearning
from handler.rote_learning import RoteLearning
from handler.spelling_learning import SpellingLearning
from handler.test_learning import TestLearning, call_with_watchdog
from utility import get_account


MODES = {
    1: ("암기", "Memorize", RoteLearning),
    2: ("리콜", "Recall", RecallLearning),
    3: ("스펠", "Spell", SpellingLearning),
    4: ("테스트", "Test", TestLearning),
}


def is_sentence(card):
    front = str(card.get("front") or "").strip()
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9']+", front)
    has_sentence_ending = bool(re.search(r"[.!?]+[\"']?$", front))
    return has_sentence_ending or len(words) >= 6


def word_data(cards):
    english = [0] + [card["front"] for card in cards]
    korean = [0] + [card["back"] for card in cards]
    details = [0] + [
        {"back": card["back"], "example": card.get("example", "")}
        for card in cards
    ]
    return [english, korean, details]


def set_favorites(driver, set_id, cards, selected_ids):
    selected_ids = {str(value) for value in selected_ids}
    changes = [
        {
            "card_id": card["card_id"],
            "favor": 1 if card["card_id"] in selected_ids else 0,
        }
        for card in cards
        if bool(card["favorite"]) != (card["card_id"] in selected_ids)
    ]
    if not changes:
        return
    result = driver.execute_async_script(
        """
        const setId = arguments[0], changes = arguments[1], done = arguments[2];
        Promise.all(changes.map(item => fetch('/Memorize/favor', {
          method: 'POST',
          credentials: 'same-origin',
          headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'},
          body: new URLSearchParams({
            set_idx: setId,
            card_idx: item.card_id,
            favor_yn: String(item.favor),
          }).toString(),
        }).then(response => ({ok: response.ok, status: response.status}))))
          .then(values => done({ok: values.every(value => value.ok), values}))
          .catch(error => done({ok: false, error: String(error)}));
        """,
        str(set_id),
        changes,
    )
    if not result or not result.get("ok"):
        raise RuntimeError(f"선택 카드 설정에 실패했습니다: {result}")
    for card in cards:
        card["favorite"] = card["card_id"] in selected_ids


def click_visible_text(driver, words):
    script = """
    const words = arguments[0];
    const candidates = Array.from(document.querySelectorAll('a, button'));
    const target = candidates.find(el => {
      const text = (el.innerText || '').trim();
      const style = getComputedStyle(el), rect = el.getBoundingClientRect();
      return words.some(word => text.includes(word)) && style.display !== 'none'
        && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    });
    if (!target) return false;
    target.click();
    return true;
    """
    return bool(driver.execute_script(script, words))


def prepare_round(driver, round_target):
    body = driver.execute_script("return document.body.innerText || '';")
    clear_values = [
        int(value) for value in re.findall(r"(\d+)\s*%\s*Clear", body, re.I)
    ]
    clear_progress = max(clear_values, default=0)
    if clear_progress >= round_target:
        print(
            f"현재 화면에 {clear_progress}% 완료가 확인되어 "
            f"{round_target}% 회차를 건너뜁니다.",
            flush=True,
        )
        return True

    if not any(marker in body for marker in ("학습이 완료", "학습 완료", "Clear", "새로 학습")):
        return False

    challenge_text = f"{round_target}% 도전"
    challenge_clicked = driver.execute_script(
        r"""
        const wanted = arguments[0];
        const candidates = Array.from(document.querySelectorAll(
          'a, button, [role="button"], [onclick], .btn'
        ));
        const target = candidates.find(el => {
          const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
          const style = getComputedStyle(el), rect = el.getBoundingClientRect();
          return text.includes(wanted) && style.display !== 'none'
            && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
        });
        if (!target) return false;
        target.click();
        return true;
        """,
        challenge_text,
    )
    if challenge_clicked:
        time.sleep(1)
        return False

    if challenge_text in body:
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.SPACE)
        time.sleep(0.6)
        return False

    reset = driver.execute_script(
        """
        const candidates = Array.from(document.querySelectorAll(
          '.btn-reset-section, a, button'
        ));
        const target = candidates.find(el => {
          const text = (el.innerText || '').trim();
          const style = getComputedStyle(el), rect = el.getBoundingClientRect();
          return (el.classList.contains('btn-reset-section') || text.includes('새로 학습하기'))
            && style.display !== 'none' && style.visibility !== 'hidden'
            && rect.width > 0 && rect.height > 0;
        });
        if (!target) return false;
        target.click();
        return true;
        """
    )
    if not reset:
        return False
    try:
        confirm = WebDriverWait(driver, 5).until(
            lambda d: d.execute_script(
                """
                const candidates = Array.from(document.querySelectorAll(
                  '.modal.in .btn-danger, .modal.show .btn-danger, .bootbox .btn-danger,'
                  + '.modal.in a, .modal.in button, .modal.show a, .modal.show button'
                ));
                return candidates.find(el => {
                  const text = (el.innerText || '').trim();
                  const style = getComputedStyle(el), rect = el.getBoundingClientRect();
                  return (text.includes('새로 학습') || text === '확인')
                    && style.display !== 'none' && style.visibility !== 'hidden'
                    && rect.width > 0 && rect.height > 0;
                }) || null;
                """
            )
        )
        driver.execute_script("arguments[0].click();", confirm)
    except TimeoutException:
        pass
    try:
        WebDriverWait(driver, 8).until(
            lambda d: any(
                element.is_displayed()
                for element in d.find_elements(By.CSS_SELECTOR, ".btn-opt-start")
            )
            or not any(
                marker in (d.execute_script("return document.body.innerText || '';") or "")
                for marker in ("학습이 완료", "학습 완료", "새로 학습하기")
            )
        )
    except TimeoutException:
        pass
    return False


def validate(payload):
    class_id = str(payload.get("class_id", ""))
    set_id = str(payload.get("set_id", ""))
    start = int(payload.get("start", 0))
    end = int(payload.get("end", 0))
    mode = int(payload.get("mode", 0))
    amount = int(payload.get("amount", 0))
    if not class_id.isdigit() or not set_id.isdigit():
        raise ValueError("클래스와 세트를 다시 선택해 주세요.")
    if start < 1 or end < 1:
        raise ValueError("카드 시작과 끝 번호는 1 이상이어야 합니다.")
    if mode not in MODES:
        raise ValueError("암기, 리콜, 스펠, 테스트 중 하나를 선택해 주세요.")
    if amount not in (1, 2, 3, 4):
        raise ValueError("100%, 200%, 300%, 400% 중 하나를 선택해 주세요.")
    return class_id, set_id, min(start, end), max(start, end), mode, amount


def run(payload):
    started_at = time.time()
    class_id, set_id, start, end, mode, amount = validate(payload)
    title = str(payload.get("title") or f"세트 {set_id}")
    mode_name, route, handler_class = MODES[mode]
    with ThreadPoolExecutor(max_workers=1) as executor:
        login_future = executor.submit(create_login_session, get_account())
        driver = make_driver()
        try:
            authenticated_session = login_future.result(timeout=20)
        except Exception:
            authenticated_session = None
    cards = []
    originals = set()
    try:
        login(driver, authenticated_session)
        open_set(driver, set_id, class_id)
        # 세트 페이지를 방금 띄웠다는 표시. 테스트 모드는 이 페이지를 그대로
        # 쓰면 되는데도 회차 루프에서 같은 주소를 한 번 더 열어 전체 페이지
        # 로딩을 중복으로 하고 있었다.
        set_page_fresh = True
        # 테스트는 진행률을 쓰지 않으므로(항상 0에서 시작) 조회를 생략한다.
        progress = {} if mode_name == "테스트" else read_learning_progress(driver)
        current_progress = 0 if mode_name == "테스트" else int(progress.get(mode_name, 0) or 0)
        target_progress = amount * 100
        remaining_rounds = max(
            0,
            (target_progress - current_progress + 99) // 100,
        )
        cards = read_cards(driver)
        if end > len(cards):
            raise ValueError(f"카드 번호는 1부터 {len(cards)}까지만 선택할 수 있습니다.")
        originals = {card["card_id"] for card in cards if card["favorite"]}
        selected = cards if mode_name == "테스트" else cards[start - 1:end]
        groups = (
            [("전체", cards)]
            if mode_name == "테스트"
            else [
                ("단어", [card for card in selected if not is_sentence(card)]),
                ("문장", [card for card in selected if is_sentence(card)]),
            ]
        )
        groups = [(label, group) for label, group in groups if group]
        card_type = "+".join(label for label, _ in groups)
        print(
            f"선택: {title} / 카드 {start}~{end} / {mode_name} {target_progress}% "
            f"(준비 {time.time() - started_at:.1f}초)",
            flush=True,
        )
        print(f"카드 종류: {card_type}", flush=True)
        print(
            "카드 구성: " + ", ".join(f"{label} {len(group)}개" for label, group in groups),
            flush=True,
        )

        if current_progress >= target_progress:
            print(
                f"현재 {mode_name} {current_progress}%로 목표 {target_progress}%를 "
                "이미 달성했습니다. 다음 카드로 넘어갑니다.",
                flush=True,
            )
            return {
                "status": "skipped",
                "title": title,
                "mode": mode_name,
                "progress": current_progress,
                "target": target_progress,
                "card_type": card_type,
            }

        print(
            f"현재 {current_progress}% -> 목표 {target_progress}% "
            f"(남은 학습 {remaining_rounds}회)",
            flush=True,
        )

        for label, group in groups:
            selected_ids = {card["card_id"] for card in group}
            if mode_name != "테스트":
                set_favorites(driver, set_id, cards, selected_ids)
            data = word_data(group)
            section = 6000 if len(group) == len(cards) else 4000
            for round_number in range(1, remaining_rounds + 1):
                if mode_name == "테스트":
                    # 앞에서 이미 연 세트 페이지가 그대로 살아 있으면 다시 열지
                    # 않는다(테스트 모드는 그 사이 페이지를 바꾸지 않는다).
                    if set_page_fresh:
                        set_page_fresh = False
                    else:
                        open_set(driver, set_id, class_id)
                else:
                    set_page_fresh = False
                    driver.get(f"https://www.classcard.net/{route}/{set_id}/{section}/{class_id}")
                    WebDriverWait(driver, 20).until(
                        lambda d: d.find_elements(By.ID, "wrapper-learn")
                        or d.find_elements(By.CSS_SELECTOR, ".CardItem")
                    )
                if label == "문장" and section == 4000:
                    body = driver.execute_script("return document.body.innerText || '';")
                    if re.search(r"\b0\s*/\s*0\b", body):
                        raise RuntimeError(
                            "클래스카드가 이 문장 세트의 선택 카드 구간을 0개로 반환했습니다. "
                            "이 전용 문장 세트는 현재 전체 카드 범위로만 실행할 수 있습니다."
                        )
                round_target = min(
                    target_progress,
                    current_progress + round_number * 100,
                )
                already_completed = mode_name != "테스트" and prepare_round(driver, round_target)
                print(
                    f"{label} {mode_name} {round_number}/{remaining_rounds}회 시작 ({len(group)}개)",
                    flush=True,
                )
                if already_completed:
                    completed = len(group)
                else:
                    completed = handler_class(driver).run(
                        num_d=len(group) + 1,
                        word_d=data,
                    )
                print(
                    f"{label} {mode_name} {round_number}/{remaining_rounds}회 완료: {completed}/{len(group)}",
                    flush=True,
                )
                time.sleep(0.4)
        print("카드 학습이 완료되었습니다.", flush=True)
        return {
            "status": "completed",
            "title": title,
            "mode": mode_name,
            "progress": target_progress,
            "target": target_progress,
            "card_type": card_type,
        }
    finally:
        # 실패 원인이 "브라우저 세션이 멈춤"인 경우, 정리 단계에서 같은 세션에
        # 명령을 보내면 여기서 다시 무한정 멈춘다(그러면 오류 메시지를 남기려던
        # 의도와 달리 워크플로 제한까지 걸린다). 정리 작업 전체에 상한을 둔다.
        def restore_favorites():
            driver.get(f"https://www.classcard.net/set/{set_id}/{class_id}")
            WebDriverWait(driver, 15).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, ".flip-card[data-idx]")
            )
            set_favorites(driver, set_id, cards, originals)

        if cards:
            try:
                call_with_watchdog(restore_favorites, 45)
                print("기존 중요 카드 표시를 복구했습니다.", flush=True)
            except Exception as error:
                print(f"중요 카드 표시 복구 경고: {error}", flush=True)
        try:
            call_with_watchdog(driver.quit, 30)
            print("브라우저를 종료했습니다.", flush=True)
        except Exception as error:
            print(f"브라우저 종료 경고: {error}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload-base64", required=True)
    args = parser.parse_args()
    raw = base64.urlsafe_b64decode(args.payload_base64.encode("ascii")).decode("utf-8")
    run(json.loads(raw))


if __name__ == "__main__":
    main()
