"""Format uploaded file names and sizes for display."""

from pathlib import PureWindowsPath


def _suffix(name: str) -> str:
    return PureWindowsPath(name).suffix.lower().lstrip(".")


_FILE_ICONS = {
    "pdf": "📄",
    "png": "🖼️",
    "jpg": "🖼️",
    "jpeg": "🖼️",
    "xlsx": "📊",
    "docx": "📝",
}


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} Б"
    kilobytes = size / 1024
    if kilobytes < 1024:
        return f"{kilobytes:.1f} КБ"
    return f"{kilobytes / 1024:.1f} МБ"


def _files_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "файл"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return "файла"
    return "файлов"


def _file_icon(name: str) -> str:
    return _FILE_ICONS.get(_suffix(name), "📎")


def _file_type_label(name: str) -> str:
    suffix = _suffix(name)
    return suffix.upper() if suffix else "ФАЙЛ"


def _display_name(name: str) -> str:
    """Экранирует markdown-символы в имени, загруженном пользователем."""

    for character in (
        "\\",
        "`",
        "*",
        "_",
        "[",
        "]",
        "(",
        ")",
        "<",
        ">",
        "#",
        "|",
        "!",
    ):
        name = name.replace(character, f"\\{character}")
    return name
