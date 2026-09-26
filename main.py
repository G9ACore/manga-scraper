import argparse
import asyncio
import sys

from playwright.async_api import async_playwright

from scraper import MangaScraper
from state_manager import (
    is_same_manga,
    load_metadata,
    load_state,
    save_metadata,
    save_state,
)


def parse_arguments():
    parser = argparse.ArgumentParser(description="Com-X.life Manga Scraper")
    parser.add_argument("url", nargs="?", help="Ссылка на страницу манги")
    parser.add_argument(
        "-sc", "--start-chapter", type=int, help="Начать с указанной главы"
    )
    parser.add_argument(
        "-um",
        "--update-metadata",
        action="store_true",
        help="Обновить метаданные и обложку",
    )
    parser.add_argument(
        "-uc", "--update-chapters", action="store_true", help="Обновить список глав"
    )

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    return parser.parse_args()


async def main():
    args = parse_arguments()

    # 1. Определяем URL
    target_url = args.url
    if not target_url:
        state = load_state()
        if state.get("last_url"):
            print(f"💡 Найдено сохраненное состояние: {state['last_manga_name']}")
            confirm = input("Продолжить работу с этой мангой? (Y/n): ").strip().lower()
            if confirm in ["", "y", "yes", "д", "да"]:
                target_url = state["last_url"]
            else:
                target_url = input("Введите новую ссылку на мангу: ").strip()
        else:
            target_url = input("Введите ссылку на главную страницу манги: ").strip()

    if not target_url.startswith("http"):
        print("❌ Некорректная ссылка.")
        sys.exit(1)

    # 2. Проверяем, изменилась ли манга
    same_manga = is_same_manga(target_url)
    save_state(target_url, "Processing")

    # 3. Инициализация скрапера
    scraper = MangaScraper(target_url)

    async with async_playwright() as p:
        browser, context = await scraper.setup_browser(p)
        page = await context.new_page()

        print("🚀 Запуск парсера...")

        # === 4. МЕТАДАННЫЕ ===
        # Обновляем если: манга новая ИЛИ явно запрошено ИЛИ файл отсутствует
        need_update_metadata = (
            args.update_metadata
            or not same_manga
            or not scraper._metadata_file.exists()
        )

        if need_update_metadata:
            print("📋 Сбор/обновление метаданных...")
            await scraper.get_metadata(page, target_url)
            await scraper.download_cover(
                page, scraper.metadata.get("cover_url", ""), scraper.output_dir
            )
            save_metadata(scraper.output_dir, scraper.metadata)
        else:
            print("⏭️  Используем сохранённые метаданные. (-um для обновления)")
            scraper.metadata = load_metadata(scraper.output_dir)

        # === 5. СПИСОК ГЛАВ ===
        # Обновляем если: манга новая ИЛИ явно запрошено ИЛИ кэш отсутствует
        need_update_chapters = (
            args.update_chapters
            or not same_manga
            or not scraper._chapters_file.exists()
        )

        if need_update_chapters:
            print("📚 Сбор списка глав...")
            scraper.cached_chapters = await scraper.get_all_chapters(page, target_url)
            scraper.save_chapters_cache()  # Сохраняем в файл
        else:
            print("⏭️  Используем кэшированный список глав. (-uc для обновления)")
            scraper.load_chapters_cache()

        # === 6. ВЫБОР ДИАПАЗОНА ===
        chapters_to_download = scraper.cached_chapters
        if args.start_chapter:
            items = list(chapters_to_download.items())
            if 1 <= args.start_chapter <= len(items):
                chapters_to_download = dict(items[args.start_chapter - 1 :])
                print(f"✅ Скачивание начнётся с главы #{args.start_chapter}")
            else:
                print("⚠️  Неверный номер главы. Скачиваем все.")

        # === 7. СКАЧИВАНИЕ ===
        from utils import format_chapter_name

        total = len(chapters_to_download)
        for idx, (title, url) in enumerate(chapters_to_download.items(), start=1):
            clean_name = format_chapter_name(title)

            print(f"\n[{idx}/{total}] ", end="")
            success = await scraper.download_chapter(page, url, clean_name, idx)
            if not success:
                print("  ⏭️  Глава пропущена из-за ошибок, переходим к следующей...")

        await browser.close()

        # === 8. ИТОГОВЫЙ ОТЧЁТ ===
        print("\n" + "=" * 60)
        if scraper.failed_chapters:
            print("⚠️  ВНИМАНИЕ: Следующие главы не были скачаны полностью:")
            for ch in scraper.failed_chapters:
                print(f"  - {ch}")
            print("💡 Совет: Просто запустите скрипт снова — он докачает только эти.")
        else:
            print("🎉 Все операции завершены успешно!")
        print(f"📁 Манга сохранена в: {scraper.output_dir.absolute()}")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
