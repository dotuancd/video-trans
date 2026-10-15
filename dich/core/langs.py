# Languages the UI offers as targets: code → (ISO 639-2 for media metadata, English name for prompts,
# name shown to the user).
LANGUAGES = {
    "vi": ("vie", "Vietnamese", "Tiếng Việt"),
    "en": ("eng", "English", "Tiếng Anh"),
    "ja": ("jpn", "Japanese", "Tiếng Nhật"),
    "ko": ("kor", "Korean", "Tiếng Hàn"),
    "zh": ("zho", "Chinese (Simplified)", "Tiếng Trung"),
    "fr": ("fra", "French", "Tiếng Pháp"),
    "de": ("deu", "German", "Tiếng Đức"),
    "es": ("spa", "Spanish", "Tiếng Tây Ban Nha"),
}


def iso3(code):
    return LANGUAGES.get(code, (None,))[0]


def english_name(code):
    return LANGUAGES.get(code, (None, code))[1] if code else "the original language"
