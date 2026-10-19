# Text keys for searching and sorting job names: without Vietnamese marks, lower case, numbers by value.
import re
import unicodedata


def fold(text):
    """Without diacritics (đ → d too) and lower case: "Tập 2 – Đà Lạt" → "tap 2 – da lat"."""
    if text is None:
        return None
    text = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def natural_key(text):
    """fold(text) with each run of digits prefixed by its length, so that plain string order is natural
    order: "Tập 2" < "Tập 10", and "Ánh" sits next to "Anh"."""
    if text is None:
        return None

    def number(match):
        digits = match.group().lstrip("0") or "0"
        return f"{len(digits):03d}{digits}"

    return re.sub(r"\d+", number, fold(text))
