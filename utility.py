import json
import os
import re
from getpass import getpass
from pathlib import Path

import requests
from selenium import webdriver
from selenium.webdriver.common.by import By


DATA_DIR = Path(
    os.environ.get("CLASSCARD_DATA_DIR", Path(__file__).resolve().parent)
).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = DATA_DIR / "config.json"


def credential_cipher():
    key = os.environ.get("CLASSCARD_CREDENTIAL_KEY", "").strip()
    if not key:
        return None
    from cryptography.fernet import Fernet

    return Fernet(key.encode("ascii"))


def decode_password(item: dict) -> str:
    if item.get("pw_enc"):
        cipher = credential_cipher()
        if cipher is None:
            raise RuntimeError("계정 암호화 키가 설정되지 않았습니다.")
        return cipher.decrypt(str(item["pw_enc"]).encode("ascii")).decode("utf-8")
    return str(item.get("pw") or "")


def normalize_card_text(value: str) -> str:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in str(value or "").splitlines()
        if re.sub(r"\s+", " ", line).strip()
    ]
    return " ".join(lines)


def primary_card_text(value: str) -> str:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in str(value or "").splitlines()
        if re.sub(r"\s+", " ", line).strip()
    ]
    return lines[0] if lines else ""


def word_get(driver: webdriver.Chrome, num_d: int) -> list:
    da_e = [0 for _ in range(num_d)]
    da_k = [0 for _ in range(num_d)]
    da_kyn = [0 for _ in range(num_d)]

    for i in range(1, num_d):
        english_element = driver.find_element(
            By.XPATH,
            f"//*[@id='tab_set_all']/div[2]/div[{i}]/div[4]/div[1]/div[1]/div/div",
        )
        english_text = (
            english_element.get_attribute("innerText")
            or english_element.get_attribute("textContent")
            or english_element.text
        )
        da_e[i] = primary_card_text(english_text)

    for i in range(1, num_d):
        card_element = driver.find_element(
            By.XPATH,
            f"//*[@id='tab_set_all']/div[2]/div[{i}]",
        )
        korean_element = driver.find_element(
            By.XPATH,
            f"//*[@id='tab_set_all']/div[2]/div[{i}]/div[4]/div[2]/div[1]/div/div",
        )
        korean_text = (
            korean_element.get_attribute("innerText")
            or korean_element.get_attribute("textContent")
            or korean_element.text
        )
        da_k[i] = primary_card_text(korean_text)
        example_text = ""
        try:
            example_element = card_element.find_element(By.CSS_SELECTOR, ".ex_example")
            example_text = (
                example_element.get_attribute("innerText")
                or example_element.get_attribute("textContent")
                or ""
            )
        except Exception:
            pass
        da_kyn[i] = {
            "back": normalize_card_text(korean_text),
            "example": str(example_text or "").strip(),
        }

    valid_count = sum(bool(english and korean) for english, korean in zip(da_e, da_k))
    if valid_count == 0:
        raise RuntimeError("세트에서 단어 또는 문장 데이터를 가져오지 못했습니다.")
    print(f"학습 데이터 {valid_count}개를 불러왔습니다.")
    return [da_e, da_k, da_kyn]


def chd_wh() -> int:  # 학습유형 선택
    os.system("cls")
    choice_dict = {
        1: "암기학습(매크로)",
        2: "리콜학습(매크로)",
        3: "스펠학습(매크로)",
        4: "테스트학습(매크로)",
        5: "암기학습(API 요청[경고])",
        6: "리콜학습(API 요청[경고])",
        7: "스펠학습(API 요청[경고])",
    }
    print(
        "학습유형을 선택해주세요.\n"
        "Ctrl + C 를 눌러 종료\n"
        "[1] 암기학습(매크로)\n"
        "[2] 리콜학습(매크로)\n"
        "[3] 스펠학습(매크로)\n"
        "[4] 테스트학습(매크로)\n"
        "[5] 암기학습(API 요청[경고])\n"
        "[6] 리콜학습(API 요청[경고])\n"
        "[7] 스펠학습(API 요청[경고])"
    )
    while 1:
        try:
            ch_d = int(input(">>> "))
            if ch_d >= 1 and ch_d <= 7:
                break
            else:
                raise ValueError
        except ValueError:
            print("학습유형을 다시 입력해주세요.")
        except KeyboardInterrupt:
            quit()
    os.system("cls")
    print(f"{ch_d}번 {choice_dict[ch_d]}를 선택하셨습니다.")
    return ch_d


def choice_set(sets: dict) -> int:  # 세트 선택
    os.system("cls")
    print("학습할 세트를 선택해주세요.")
    print("Ctrl + C 를 눌러 종료")
    for set_item in sets:
        print(
            f"[{set_item+1}] {sets[set_item].get('title')} | {sets[set_item].get('card_num')}"
        )
    while True:
        try:
            ch_s = int(input(">>> "))
            if ch_s >= 1 and ch_s <= len(sets):
                break
            else:
                raise ValueError
        except ValueError:
            print("세트를 다시 입력해주세요.")
        except KeyboardInterrupt:
            quit()
    os.system("cls")
    print(f"{sets[ch_s-1].get('title')}를 선택하셨습니다.")
    return ch_s - 1


def choice_class(class_dict: dict) -> int:  # 학습할 반 선택
    os.system('cls' if os.name == 'nt' else 'clear')
    print("학습할 클래스를 선택해주세요.")
    print("Ctrl + C 를 눌러 종료")
    for class_item in class_dict:
        print(f"[{class_item+1}] {class_dict[class_item].get('class_name')}")
    while True:
        try:
            ch_c = int(input(">>> "))
            if ch_c >= 1 and ch_c <= len(class_dict):
                break
            else:
                raise ValueError
        except ValueError:
            print("클래스를 다시 입력해주세요.")
        except KeyboardInterrupt:
            quit()
    os.system("cls")
    print(f"{class_dict[ch_c-1].get('class_name')}를 선택하셨습니다.")
    return ch_c - 1


def check_id(id: str, pw: str) -> bool:
    print("계정 정보를 확인하고 있습니다 잠시만 기다려주세요!")
    headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
    data = {"login_id": id, "login_pwd": pw}
    try:
        res = requests.post(
            "https://www.classcard.net/LoginProc",
            headers=headers,
            data=data,
            timeout=20,
        )
        status = res.json()
        return status.get("result") == "ok"
    except Exception as error:
        print(f"계정 확인 중 오류가 발생했습니다: {error}")
        return False


def load_account_config() -> dict:
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"accounts": [], "selected": 0}

    if isinstance(raw, dict) and raw.get("id") and raw.get("pw"):
        account_id = str(raw["id"])
        return {
            "accounts": [
                {"name": account_id, "id": account_id, "pw": str(raw["pw"])}
            ],
            "selected": 0,
        }

    accounts = []
    for item in raw.get("accounts", []) if isinstance(raw, dict) else []:
        if (
            not isinstance(item, dict)
            or not item.get("id")
            or not (item.get("pw") or item.get("pw_enc"))
        ):
            continue
        account_id = str(item["id"])
        password = decode_password(item)
        if not password:
            continue
        accounts.append(
            {
                "name": str(item.get("name") or account_id),
                "id": account_id,
                "pw": password,
            }
        )
    selected = int(raw.get("selected", 0) or 0) if isinstance(raw, dict) else 0
    if not 0 <= selected < len(accounts):
        selected = 0
    return {"accounts": accounts, "selected": selected}


def save_account_config(config: dict) -> None:
    cipher = credential_cipher()
    stored_accounts = []
    for account in config.get("accounts", []):
        stored = {"name": account["name"], "id": account["id"]}
        password = str(account.get("pw") or "")
        if cipher is None:
            stored["pw"] = password
        else:
            stored["pw_enc"] = cipher.encrypt(password.encode("utf-8")).decode("ascii")
        stored_accounts.append(stored)
    stored_config = {
        "accounts": stored_accounts,
        "selected": int(config.get("selected", 0) or 0),
    }
    CONFIG_PATH.write_text(
        json.dumps(stored_config, ensure_ascii=False, indent=4),
        encoding="utf-8",
    )
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass


def account_summaries() -> list:
    config = load_account_config()
    return [
        {
            "index": index,
            "name": account["name"],
            "id": account["id"],
            "selected": index == config["selected"],
        }
        for index, account in enumerate(config["accounts"])
    ]


def select_account(index: int) -> dict:
    config = load_account_config()
    if not 0 <= int(index) < len(config["accounts"]):
        raise ValueError("선택할 계정을 찾지 못했습니다.")
    config["selected"] = int(index)
    save_account_config(config)
    return config["accounts"][int(index)]


def save_verified_account(account_id: str, password: str, name: str = "") -> dict:
    account_id = str(account_id or "").strip()
    password = str(password or "")
    name = str(name or "").strip() or account_id
    if not account_id or not password:
        raise ValueError("아이디와 비밀번호를 모두 입력해 주세요.")
    if not check_id(account_id, password):
        raise ValueError("아이디 또는 비밀번호가 잘못되었습니다.")

    config = load_account_config()
    existing_index = next(
        (
            index
            for index, item in enumerate(config["accounts"])
            if item["id"] == account_id
        ),
        None,
    )
    account = {"name": name, "id": account_id, "pw": password}
    if existing_index is None:
        config["accounts"].append(account)
        existing_index = len(config["accounts"]) - 1
    else:
        config["accounts"][existing_index] = account
    config["selected"] = existing_index
    save_account_config(config)
    return account


def add_account(config=None) -> dict:
    config = config or load_account_config()
    while True:
        account_id = input("아이디를 입력하세요: ").strip()
        password = getpass("비밀번호를 입력하세요: ")
        if not account_id or not password:
            print("아이디와 비밀번호를 모두 입력해 주세요.\n")
            continue
        if not check_id(account_id, password):
            print("아이디 또는 비밀번호가 잘못되었습니다.\n")
            continue

        default_name = account_id
        name = input(f"계정 이름 (기본값: {default_name}): ").strip() or default_name
        existing = next(
            (item for item in config["accounts"] if item["id"] == account_id),
            None,
        )
        account = {"name": name, "id": account_id, "pw": password}
        if existing is None:
            config["accounts"].append(account)
            selected = len(config["accounts"]) - 1
        else:
            selected = config["accounts"].index(existing)
            config["accounts"][selected] = account
        config["selected"] = selected
        save_account_config(config)
        print(f"{name} 계정을 저장하고 선택했습니다.\n")
        return account


def choose_account() -> dict:
    config = load_account_config()
    if not config["accounts"]:
        print("저장된 계정이 없습니다. 새 계정을 추가합니다.\n")
        return add_account(config)

    while True:
        print("\n클래스카드 계정을 선택하세요.")
        for number, account in enumerate(config["accounts"], start=1):
            marker = " *" if number - 1 == config["selected"] else ""
            label = account["name"]
            account_text = account["id"] if label == account["id"] else f"{label} ({account['id']})"
            print(f"[{number}] {account_text}{marker}")
        print("[A] 새 계정 추가")

        raw = input(">>> ").strip()
        if raw.casefold() == "a":
            return add_account(config)
        if raw.isdigit() and 1 <= int(raw) <= len(config["accounts"]):
            selected = int(raw) - 1
            config["selected"] = selected
            save_account_config(config)
            account = config["accounts"][selected]
            print(f"{account['name']} 계정을 선택했습니다.\n")
            return account
        print("계정 번호 또는 A를 입력해 주세요.")


def save_id() -> dict:
    return add_account(load_account_config())


def classcard_api_post(
    user_id: int,
    set_id: int,
    class_id: int,
    view_cnt: int,
    activity: int,
) -> None:
    url = "https://www.classcard.net/ViewSetAsync/resetAllLog"
    payload = f"set_idx={set_id}&activity={activity}&user_idx={user_id}&view_cnt={view_cnt}&class_idx={class_id}"
    headers = {
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    requests.request("POST", url, data=payload, headers=headers)


def get_account() -> dict:
    config = load_account_config()
    if not config["accounts"]:
        env_id = os.environ.get("CLASSCARD_ID", "").strip()
        env_password = os.environ.get("CLASSCARD_PASSWORD", "")
        if env_id and env_password:
            account = {"name": env_id, "id": env_id, "pw": env_password}
            config = {"accounts": [account], "selected": 0}
            save_account_config(config)
            return account
        if os.environ.get("CLASSCARD_NONINTERACTIVE") == "1":
            raise RuntimeError("저장된 클래스카드 계정이 없습니다.")
        return save_id()
    return config["accounts"][config["selected"]]
