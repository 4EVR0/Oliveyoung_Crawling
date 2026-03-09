def make_product_dict(url, main_cat, sub_cat) -> dict:
    return {
        "url":           canonicalize_goods_url(url),
        "main_category": main_cat,
        "sub_category":  sub_cat,
        "name":          "",
        "brand":         "",
        "price":         "",
        "ingredients":   "",
        "product_info":  {},
        "rating":        "",
        "review_count":  "",
        "review_stats":  {},   # ← 고정 구조 대신 빈 딕셔너리
        "crawled_at":    datetime.now().isoformat(),
    }
