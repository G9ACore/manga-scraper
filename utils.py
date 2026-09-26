from xml.etree.ElementTree import Element, SubElement, tostring


def format_chapter_name(title: str) -> str:
    """Очищает название главы от недопустимых символов и пробелов в конце"""
    # 1. Убираем пробелы в начале и в конце (РЕШЕНИЕ ПРОБЛЕМЫ С ПРОБЕЛОМ)
    clean_title = title.strip()
    # 2. Заменяем опасные символы
    clean_title = clean_title.replace("/", "_").replace(":", "-").replace("\\", "_")
    return clean_title


def create_comicinfo_xml(
    series_name: str, chapter_title: str, chapter_number: int, metadata: dict
) -> bytes:
    """Создаёт ComicInfo.xml"""
    root = Element("ComicInfo")
    SubElement(root, "Title").text = chapter_title
    SubElement(root, "Series").text = series_name
    SubElement(root, "Number").text = str(chapter_number)

    if metadata.get("author"):
        SubElement(root, "Writer").text = metadata["author"]
    if metadata.get("artist"):
        SubElement(root, "Penciller").text = metadata["artist"]
    if metadata.get("publisher"):
        SubElement(root, "Publisher").text = metadata["publisher"]
    if metadata.get("year"):
        SubElement(root, "Year").text = metadata["year"]
    if metadata.get("genres"):
        SubElement(root, "Genre").text = ", ".join(metadata["genres"])
    if metadata.get("description"):
        SubElement(root, "Summary").text = metadata["description"]

    SubElement(root, "Manga").text = "YesAndRightToLeft"
    SubElement(root, "LanguageISO").text = "ru"

    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(
        root, encoding="unicode"
    ).encode("utf-8")
