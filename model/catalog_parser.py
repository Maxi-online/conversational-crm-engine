"""Neutral landing-page context and UTM parsing."""

from urllib.parse import parse_qs, urlparse, unquote


def program_name_and_description_parser(url):
    path = urlparse(url or "").path
    pieces = [unquote(piece) for piece in path.split("/") if piece]
    return pieces[-1].replace("-", " ").replace("_", " ")[:200] if pieces else "General catalog"


def extract_utm_params(url):
    query = parse_qs(urlparse(url or "").query)
    return {
        key: query[key][0][:200]
        for key in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
        if key in query
    }
