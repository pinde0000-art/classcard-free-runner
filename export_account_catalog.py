"""계정 하나의 프로필과 클래스·세트 목록을 읽어 Worker에 전달한다.

기존 export_catalog.py(단일 계정 · docs/catalog.json)는 그대로 두고,
계정 모드에서만 이 스크립트를 쓴다. 목록 수집은 검증된
classcard_catalog.discover_catalog()를 그대로 재사용한다.
"""

import os
import re
import sys

from classcard_catalog import discover_catalog, make_driver
from runner_account import load_credentials, report_profile


NICKNAME_SCRIPT = r"""
// 로그인 후 화면에서 표시용 닉네임과 프로필 이미지를 찾는다.
// 선택자가 바뀌어도 실패하지 않도록 후보를 넓게 두고, 못 찾으면 빈 값을 돌려준다.
const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
const nameSelectors = [
  '.user-name', '.username', '.user_name', '.profile-name',
  '.my-name', '.name', '#user_name',
  '.gnb-user .name', '.header-user .name', '[class*="user"] [class*="name"]',
];
let nickname = '';
for (const selector of nameSelectors) {
  for (const element of document.querySelectorAll(selector)) {
    const text = clean(element.innerText || element.textContent);
    // 메뉴 문구나 지나치게 긴 문자열은 이름이 아니다.
    if (text && text.length <= 20 && !/로그인|로그아웃|마이|설정|클래스카드/.test(text)) {
      nickname = text;
      break;
    }
  }
  if (nickname) break;
}
let avatar = '';
const imageSelectors = [
  '.user-photo img', '.profile-image img', '.user-thumb img',
  'img.profile', 'img[class*="profile"]', 'img[class*="user"]',
  '[class*="photo"] img',
];
for (const selector of imageSelectors) {
  const image = document.querySelector(selector);
  const source = image && (image.currentSrc || image.src);
  if (source && !/blank|default|noimg|no_img/i.test(source)) {
    avatar = source;
    break;
  }
}
return {nickname, avatar};
"""


def parse_count(value):
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else 0


def read_profile():
    """닉네임·프로필 이미지는 별도 세션에서 가볍게 읽는다."""
    from batch_learning import login

    driver = make_driver()
    try:
        login(driver)
        driver.get("https://www.classcard.net/")
        result = driver.execute_script(NICKNAME_SCRIPT) or {}
        return str(result.get("nickname") or ""), str(result.get("avatar") or "")
    finally:
        driver.quit()


def collect_classes():
    classes = []
    for item in discover_catalog():
        classes.append(
            {
                "id": str(item["class_id"]),
                "name": str(item["class_name"]),
                "sets": [
                    {
                        "id": str(entry["set_id"]),
                        "name": str(entry["title"]),
                        "count": parse_count(entry.get("count")),
                    }
                    for entry in item.get("sets", [])
                ],
            }
        )
    return classes


def main():
    account_id = os.environ.get("INPUT_ACCOUNT_ID", "").strip()
    sync_id = os.environ.get("INPUT_SYNC_ID", "").strip()
    if not account_id or not sync_id:
        raise RuntimeError("계정 동기화에 필요한 값이 없습니다.")

    load_credentials(sync_id)

    try:
        nickname, avatar = read_profile()
        print(
            f"프로필 확인: 닉네임 {len(nickname)}자"
            f"{' (첫 글자 ' + nickname[0] + ')' if nickname else ' (찾지 못함)'}, "
            f"프로필 이미지 {'있음' if avatar else '없음'}",
            flush=True,
        )
        classes = collect_classes()
        if not classes:
            raise RuntimeError("클래스 목록을 찾지 못했습니다.")
        report_profile(account_id, nickname=nickname, avatar=avatar, classes=classes)
        print(
            f"클래스 {len(classes)}개, 세트 "
            f"{sum(len(item['sets']) for item in classes)}개를 계정에 저장했습니다.",
            flush=True,
        )
    except Exception as error:
        message = str(error)[:180]
        try:
            report_profile(account_id, error=message, status="error")
        except Exception:
            pass
        print(f"계정 동기화 실패: {message}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
