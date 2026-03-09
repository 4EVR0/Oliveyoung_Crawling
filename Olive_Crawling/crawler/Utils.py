from urllib.parse import urlparse, parse_qs, urlencode, urlunparse


def canonicalize_goods_url(url: str) -> str:
    """
    올리브영 상세 URL 에서 goodsNo 파라미터만 남겨 정규화.
    t_number 같은 추적 파라미터가 매번 달라지는 문제를 방지한다.
    """
    try:
        parsed  = urlparse(url)
        qs      = parse_qs(parsed.query)
        goods_no = (qs.get("goodsNo") or [None])[0]
        if not goods_no:
            return url
        new_query = urlencode({"goodsNo": goods_no})
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", new_query, ""))
    except Exception:
        return url


def safe_name(text: str) -> str:
    """카테고리명을 파일/S3 경로에 안전한 문자열로 변환"""
    return text.replace(" ", "_").replace("/", "-")



