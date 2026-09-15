"""Shared identity rules for browser results and spreadsheet snapshots."""

import re
import unicodedata
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit


EMAIL_RE = re.compile(
    r"(?i)(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![\w.-])"
)
SOCIAL_HOSTS = {
    "instagram.com", "tiktok.com", "youtube.com", "youtu.be",
    "facebook.com", "x.com", "linkedin.com", "pinterest.com",
    "twitch.tv", "threads.net", "snapchat.com", "vk.com",
    "vimeo.com", "t.me",
}
HOST_ALIASES = {
    "twitter.com": "x.com", "fb.com": "facebook.com",
    "telegram.me": "t.me", "threads.com": "threads.net",
    "m.youtube.com": "youtube.com", "m.facebook.com": "facebook.com",
    "mobile.twitter.com": "x.com", "mobile.x.com": "x.com",
}
CASE_INSENSITIVE_PATHS = {
    "instagram.com", "tiktok.com", "x.com", "facebook.com",
    "t.me", "threads.net", "twitch.tv",
}


def normalize_email(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = value.replace("\u200b", "").replace("\ufeff", "").strip()
    if value.lower().startswith("mailto:"):
        value = value[7:].split("?", 1)[0]
    return re.sub(r"\s+", "", value).lower()


def normalize_social_url(value: str) -> str:
    value = str(value or "").strip().lstrip("'")
    value = value.replace("\u200b", "").replace("\ufeff", "")
    if not value or re.search(r"\s", value):
        return ""
    if value.startswith("//"):
        value = "https:" + value
    elif not re.match(r"^[a-z][a-z0-9+.-]*://", value, re.I):
        value = "https://" + value
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or parts.username or parts.password:
            return ""
        host = (parts.hostname or "").lower().removeprefix("www.")
        host = HOST_ALIASES.get(host, host)
        if host not in SOCIAL_HOSTS or parts.port not in {None, 80, 443}:
            return ""
        path = re.sub(r"%([0-9a-fA-F]{2})", lambda m: (
            chr(int(m[1], 16)) if chr(int(m[1], 16)) in
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
            else "%" + m[1].upper()
        ), parts.path).rstrip("/")
        if not path:
            return ""
        if host in CASE_INSENSITIVE_PATHS or (host == "youtube.com" and path.startswith("/@")):
            path = path.lower()
        # These query fields identify an account/video, unlike tracking parameters.
        identity_fields = {"id"} if host == "facebook.com" and path == "/profile.php" else set()
        if host == "youtube.com" and path == "/watch":
            identity_fields = {"v"}
        query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if k in identity_fields))
        return urlunsplit(("https", host, path, query, ""))
    except ValueError:
        return ""


def extract_pairs(matrix: list[list[str]]) -> set[tuple[str, str]]:
    pairs = set()
    for row_number, row in enumerate(matrix, 1):
        urls, emails = [], []
        for column, raw in enumerate(row):
            cell = str(raw or "").strip()
            for email in EMAIL_RE.findall(cell):
                item = (column, normalize_email(email))
                if item not in emails:
                    emails.append(item)
            candidates = [cell] if normalize_social_url(cell) else re.findall(r"https?://[^\s<>\"']+", cell, re.I)
            for candidate in candidates:
                social = normalize_social_url(candidate.rstrip(",.;)"))
                if social and (column, social) not in urls:
                    urls.append((column, social))
        # Empty-contact keys also record that a profile already exists with emails.
        pairs.update((social, "") for _, social in urls)
        unique_urls = {social for _, social in urls}
        if len(unique_urls) == 1:
            pairs.update((next(iter(unique_urls)), email) for _, email in emails)
        elif len(unique_urls) > 1 and emails:
            for email_column, email in emails:
                distance = min(abs(column - email_column) for column, _ in urls)
                nearest = {social for column, social in urls if abs(column - email_column) == distance}
                if len(nearest) != 1:
                    raise ValueError(f"Ambiguous social URL/email mapping on spreadsheet row {row_number}")
                pairs.add((nearest.pop(), email))
    return pairs


def filter_new_rows(rows, existing):
    """Keep new pairs; an empty contact is useful only for an unknown profile."""
    known = set(existing)
    result = []
    prepared = []
    for raw in rows:
        row = list(raw) + [""] * max(0, 3 - len(raw))
        social, email = normalize_social_url(row[1]), normalize_email(row[2])
        if not social or (email and not EMAIL_RE.fullmatch(email)):
            raise ValueError("Invalid social URL or email in a candidate row")
        name = re.sub(r"[\t\r\n]+", " ", str(row[0] or ""))
        prepared.append([name, social, email])
    profiles_with_emails = {row[1] for row in prepared if row[2]}
    for row in prepared:
        social, email = row[1:]
        key = social, email
        if key in known or (not email and (social in profiles_with_emails or any(s == social for s, _ in known))):
            continue
        result.append(row)
        known.add(key)
        known.add((social, ""))
    return result
