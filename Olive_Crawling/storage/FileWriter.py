import json
import csv

def save_json(products: list, filename: str):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
    print(f"💾 JSON 저장: {filename} ({len(products)}개)")

def save_csv(products: list, filename: str):
    if not products:
        print("저장할 상품이 없습니다.")
        return

    info_keys    = sorted({k for p in products for k in p.get("product_info", {})})
    base_headers = ["name", "brand", "main_category", "sub_category",
                    "price", "image_url", "image_s3_key", "ingredients",
                    "url", "goods_no", "crawled_at"]

    with open(filename, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(base_headers + info_keys)
        for p in products:
            row = [p.get(h, "") for h in base_headers]
            row += [p.get("product_info", {}).get(k, "") for k in info_keys]
            writer.writerow(row)

    print(f"💾 CSV 저장: {filename} ({len(products)}개)")
