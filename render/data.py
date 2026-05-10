"""Static data tables and small data-only helpers used across the render package."""
import re

IMAGE_FIELD_PREFIXES = frozenset({
    'image', 'img', 'logo', 'flag', 'coat', 'map', 'photo', 'picture',
    'banner', 'seal', 'shield', 'emblem', 'signature', 'sound', 'audio',
    'video',
})

IMAGE_VALUE_RE = re.compile(
    r'^\s*\S+\.(jpe?g|png|svg|gif|webp|tiff?|ogg|ogv|oga|wav|mp[34]|flac|webm)\s*$',
    re.IGNORECASE,
)

MONTH_NAMES = (
    '', 'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
)

CITE_TEMPLATE_PREFIXES = ('cite ', 'citation')

MATH_TEMPLATE_NAMES = {"math", "mvar", "math block", "bigmath"}

# Map of indicator template -> (display text, css class)
INDICATORS: dict[str, tuple[str, str]] = {
    'yes': ('Yes', 'indicator-yes'),
    'y': ('Yes', 'indicator-yes'),
    'tick': ('Yes', 'indicator-yes'),
    'checked': ('Yes', 'indicator-yes'),
    'no': ('No', 'indicator-no'),
    'n': ('No', 'indicator-no'),
    'x': ('No', 'indicator-no'),
    'cross': ('No', 'indicator-no'),
    'partial': ('Partial', 'indicator-partial'),
    'some': ('Partial', 'indicator-partial'),
    'dunno': ('Unknown', 'indicator-unknown'),
    'unknown': ('Unknown', 'indicator-unknown'),
    '?': ('Unknown', 'indicator-unknown'),
    'n/a': ('N/A', 'indicator-na'),
    'na': ('N/A', 'indicator-na'),
    'included': ('Included', 'indicator-yes'),
    'dropped': ('Dropped', 'indicator-no'),
    'pending': ('Pending', 'indicator-partial'),
}

LANG_NAMES: dict[str, str] = {
    'af': 'Afrikaans', 'ar': 'Arabic', 'az': 'Azerbaijani',
    'be': 'Belarusian', 'bg': 'Bulgarian', 'bn': 'Bengali',
    'bs': 'Bosnian', 'ca': 'Catalan', 'cs': 'Czech',
    'cy': 'Welsh', 'da': 'Danish', 'de': 'German',
    'el': 'Greek', 'eo': 'Esperanto', 'es': 'Spanish',
    'et': 'Estonian', 'eu': 'Basque', 'fa': 'Persian',
    'fi': 'Finnish', 'fr': 'French', 'ga': 'Irish',
    'gl': 'Galician', 'gu': 'Gujarati', 'he': 'Hebrew',
    'hi': 'Hindi', 'hr': 'Croatian', 'hu': 'Hungarian',
    'hy': 'Armenian', 'id': 'Indonesian', 'is': 'Icelandic',
    'it': 'Italian', 'ja': 'Japanese', 'ka': 'Georgian',
    'kk': 'Kazakh', 'km': 'Khmer', 'kn': 'Kannada',
    'ko': 'Korean', 'ku': 'Kurdish', 'ky': 'Kyrgyz',
    'la': 'Latin', 'lb': 'Luxembourgish', 'lt': 'Lithuanian',
    'lv': 'Latvian', 'mk': 'Macedonian', 'ml': 'Malayalam',
    'mn': 'Mongolian', 'mr': 'Marathi', 'ms': 'Malay',
    'mt': 'Maltese', 'my': 'Burmese', 'nb': 'Norwegian Bokmål',
    'ne': 'Nepali', 'nl': 'Dutch', 'nn': 'Norwegian Nynorsk',
    'no': 'Norwegian', 'pa': 'Punjabi', 'pl': 'Polish',
    'ps': 'Pashto', 'pt': 'Portuguese', 'ro': 'Romanian',
    'ru': 'Russian', 'sc': 'Sardinian', 'sd': 'Sindhi',
    'si': 'Sinhala', 'sk': 'Slovak', 'sl': 'Slovenian',
    'sq': 'Albanian', 'sr': 'Serbian', 'sv': 'Swedish',
    'sw': 'Swahili', 'ta': 'Tamil', 'te': 'Telugu',
    'tg': 'Tajik', 'th': 'Thai', 'tk': 'Turkmen',
    'tl': 'Filipino', 'tr': 'Turkish', 'tt': 'Tatar',
    'uk': 'Ukrainian', 'ur': 'Urdu', 'uz': 'Uzbek',
    'vi': 'Vietnamese', 'yi': 'Yiddish', 'zh': 'Chinese',
    'zu': 'Zulu',
}


def lang_code_to_name(code: str) -> str:
    """Return the English name for an ISO 639 language code, or the code itself."""
    base = code.lower().split('-')[0].split('_')[0]
    return LANG_NAMES.get(base, code)
