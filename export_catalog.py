import json
import re
from datetime import datetime, timezone
from pathlib import Path

from classcard_catalog import discover_catalog


OUTPUT = Path(__file__).resolve().parent / "docs" / "catalog.json"


def parse_count(value):
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else 0


def main():
    catalog = []
    for item in discover_catalog():
        sets = [
            {
                "id": str(entry["set_id"]),
                "name": str(entry["title"]),
                "count": parse_count(entry.get("count")),
            }
            for entry in item.get("sets", [])
        ]
        catalog.append(
            {
                "id": str(item["class_id"]),
                "name": str(item["class_name"]),
                "sets": sets,
            }
        )

    if not catalog:
        raise RuntimeError("클래스카드 계정에서 클래스 목록을 찾지 못했습니다.")
    if not any(item["sets"] for item in catalog):
        raise RuntimeError("클래스카드 계정에서 세트 목록을 찾지 못했습니다.")

    OUTPUT.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "classes": catalog,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"클래스 {len(catalog)}개, 세트 "
        f"{sum(len(item['sets']) for item in catalog)}개를 저장했습니다.",
        flush=True,
    )


if __name__ == "__main__":
    main()
