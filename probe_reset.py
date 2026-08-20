"""세트 페이지에 학습 기록을 되돌리는 메뉴가 있는지 살펴본다.

아무것도 누르지 않고 읽기만 한다. 초기화 방법을 확인한 뒤 지우기 위한
일회용 조사 스크립트다.
"""
import os

from batch_learning import login, open_set
from classcard_catalog import make_driver

CLASS_ID = os.environ.get("PROBE_CLASS_ID", "")
SET_ID = os.environ.get("PROBE_SET_ID", "")

LIST_CONTROLS = """
return Array.from(document.querySelectorAll('a, button, [role="button"], li'))
    .filter(el => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    })
    .map(el => {
        const text = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        return text ? text.slice(0, 40) + ' [' + (el.className || '') + ']' : '';
    })
    .filter(Boolean)
    .filter((v, i, a) => a.indexOf(v) === i)
    .slice(0, 60);
"""

# 메뉴 안에 숨어 있을 수 있으므로 감춰진 것까지 훑는다.
FIND_RESET_WORDS = """
const words = ['초기화', '리셋', '기록 삭제', '학습 기록', '처음부터', '되돌리'];
return Array.from(document.querySelectorAll('*'))
    .filter(el => el.children.length === 0)
    .map(el => (el.textContent || '').replace(/\\s+/g, ' ').trim())
    .filter(text => text && words.some(word => text.includes(word)))
    .filter((v, i, a) => a.indexOf(v) === i)
    .slice(0, 40);
"""


def main():
    driver = make_driver()
    try:
        login(driver)
        open_set(driver, SET_ID, CLASS_ID)
        print("=== 세트 페이지 URL ===", flush=True)
        print(driver.current_url, flush=True)

        print("=== 보이는 메뉴/버튼 ===", flush=True)
        for item in driver.execute_script(LIST_CONTROLS):
            print(f"  {item}", flush=True)

        print("=== 초기화 관련 문구 (숨김 포함) ===", flush=True)
        found = driver.execute_script(FIND_RESET_WORDS)
        for item in found:
            print(f"  {item!r}", flush=True)
        if not found:
            print("  (없음)", flush=True)

        # 설정/더보기 메뉴를 열면 나오는 항목이 있는지도 본다.
        opened = driver.execute_script(
            """
            const menu = document.querySelector(
                '.set-body .dropdown > a, .dropdown-toggle, .btn-top-menu a'
            );
            if (!menu) return false;
            menu.click();
            return true;
            """
        )
        print(f"=== 메뉴 열기 시도: {opened} ===", flush=True)
        if opened:
            import time

            time.sleep(1)
            for item in driver.execute_script(LIST_CONTROLS):
                print(f"  {item}", flush=True)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
