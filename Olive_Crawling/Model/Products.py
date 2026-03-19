from crawler.Utils import canonicalize_goods_url


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
        "review_stats":  {},
        "crawled_at":    "",   # 파싱 완료 후 UTC로 채움
    }
