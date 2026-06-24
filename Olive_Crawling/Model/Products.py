from crawler.Utils import canonicalize_goods_url, extract_goods_no


def make_product_dict(url, main_cat, sub_cat) -> dict:
    canonical_url = canonicalize_goods_url(url)
    return {
        "url":           canonical_url,
        "goods_no":      extract_goods_no(canonical_url),
        "main_category": main_cat,
        "sub_category":  sub_cat,
        "name":          "",
        "brand":         "",
        "price":         "",
        "image_url":     "",
        "image_s3_key":  "",
        "ingredients":   "",
        "product_info":  {},
        "rating":        "",
        "review_count":  "",
        "review_stats":  {},
        "crawled_at":    "",   # 파싱 완료 후 UTC로 채움
    }
