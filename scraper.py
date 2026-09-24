import asyncio
import os
import random
import re
import zipfile
import shutil
from pathlib import Path
from playwright_stealth import Stealth
from xml.etree.ElementTree import Element, SubElement, tostring
from playwright.async_api import async_playwright
from tqdm import tqdm

# --- КОНФИГУРАЦИЯ ---
OUTPUT_DIR = Path("./downloads")
OUTPUT_DIR.mkdir(exist_ok=True)

async def setup_browser(p):
    """Настройка браузера с обходом защиты"""
    browser = await p.firefox.launch(headless=True) # Поменяй на True, когда всё отладишь
    
    context = await browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0',
        locale='ru-RU',
        timezone_id='Europe/Moscow'
    )

    stealth = Stealth(navigator_languages_override=("ru-RU", "ru"), init_scripts_only=True)
    await stealth.apply_stealth_async(context)
    
    return browser, context

async def close_popups(page, timeout_ms=20000):
    """Универсальная функция для закрытия надоедливых окон"""
    await page.wait_for_timeout(1000)
    
    try:
        close_btn = page.get_by_role("button", name=re.compile(r"Закрыть", re.IGNORECASE)).first
        await close_btn.wait_for(state="visible", timeout=timeout_ms)
        
        await close_btn.click()
        print(f"    🚪 Попап закрыт")
        await page.wait_for_timeout(500)
        return  # Выходим из функции, попап закрыт
    except Exception:
        pass

    # "Ядерный" вариант: принудительно скрываем все элементы, похожие на модалки, через JS
    await page.evaluate("""
        () => {
            const modals = document.querySelectorAll('.modal, .un-x, .popup, .tooltip, [class*="popup"], [class*="modal"]');
            modals.forEach(el => el.style.display = 'none');
        }
    """)
    await page.wait_for_timeout(500)

async def get_manga_metadata(page, manga_url: str) -> dict:
    """Парсит метаданные манги (автор, издатель, жанры, обложка) с главной страницы"""
    print(f"📋 Сбор метаданных манги...")
    
    await page.goto(manga_url, wait_until="domcontentloaded", timeout=60000)
    await close_popups(page)
    
    metadata = {
        "author": "",
        "artist": "",
        "publisher": "",
        "year": "",
        "status": "",
        "genres": [],
        "cover_url": "",
        "description": ""
    }
    
    # 1. Парсим список метаданных (Автор, Издатель, Год, Статус)
    # Берем весь текст из li и разделяем по первому двоеточию. 
    # Это работает И для ссылок, И для обычного текста!
    meta_items = await page.locator("ul.page__list li").evaluate_all("""
        elements => elements.map(el => {
            const fullText = el.innerText.trim();
            const colonIndex = fullText.indexOf(':');
            
            if (colonIndex === -1) return null; // Если двоеточия нет, пропускаем
            
            const type = fullText.substring(0, colonIndex).trim().toLowerCase();
            const value = fullText.substring(colonIndex + 1).trim();
            
            return { type, value };
        }).filter(item => item !== null && item.type && item.value)
    """)
    
    for item in meta_items:
        item_type = item['type']
        value = item['value']
        
        # Сопоставляем типы (на случай разных написаний)
        if 'автор' in item_type or 'author' in item_type:
            metadata["author"] = value
        elif 'художник' in item_type or 'artist' in item_type or 'иллюстратор' in item_type:
            metadata["artist"] = value
        elif 'издатель' in item_type or 'publisher' in item_type:
            metadata["publisher"] = value
        elif 'год' in item_type or 'year' in item_type:
            metadata["year"] = value
        elif 'статус' in item_type or 'status' in item_type:
            metadata["status"] = value
    
    # Если художник не указан отдельно, используем автора
    if not metadata["artist"]:
        metadata["artist"] = metadata["author"]
    
    print(f"    ✍️  Автор: {metadata['author']}")
    print(f"    🎨 Художник: {metadata['artist']}")
    print(f"    🏢 Издатель: {metadata['publisher']}")
    print(f"    📅 Год: {metadata['year']}")
    print(f"    📊 Статус: {metadata['status']}")
    
    # 2. Парсим жанры/теги
    genres = await page.locator("div.page__tags > a").evaluate_all("""
        elements => elements.map(el => el.innerText.trim()).filter(t => t.length > 0)
    """)
    metadata["genres"] = genres
    print(f"    🏷️  Жанры: {', '.join(genres[:5])}{'...' if len(genres) > 5 else ''}")
    
    # 3. Парсим URL обложки
    # Ищем изображение, которое выглядит как постер/обложка
    cover_url = await page.locator("img.manga-cover, .page__poster img, .poster img, img[src*='uploads/posts']").first.get_attribute("src")
    if cover_url:
        # Если URL относительный, делаем его абсолютным
        if cover_url.startswith("/"):
            from urllib.parse import urljoin
            cover_url = urljoin(manga_url, cover_url)
        metadata["cover_url"] = cover_url
        print(f"    🖼️  Обложка: {cover_url}")
    
    # 4. Парсим описание
    try:
        description = await page.locator("div.page__text, .page__description, .description").first.inner_text(timeout=3000)
        metadata["description"] = description.strip()[:500]  # Обрезаем до 500 символов для XML
    except Exception:
        metadata["description"] = ""
    
    return metadata

def create_comicinfo_xml(series_name: str, chapter_title: str, chapter_number: int, metadata: dict) -> bytes:
    """Создаёт ComicInfo.xml с реальными метаданными манги"""
    root = Element("ComicInfo")
    
    SubElement(root, "Title").text = chapter_title
    SubElement(root, "Series").text = series_name
    SubElement(root, "Number").text = str(chapter_number)
    
    # Автор и художник
    if metadata.get("author"):
        SubElement(root, "Writer").text = metadata["author"]
    if metadata.get("artist"):
        SubElement(root, "Penciller").text = metadata["artist"]
    
    # Издатель и год
    if metadata.get("publisher"):
        SubElement(root, "Publisher").text = metadata["publisher"]
    if metadata.get("year"):
        SubElement(root, "Year").text = metadata["year"]
    
    # Жанры через запятую
    if metadata.get("genres"):
        SubElement(root, "Genre").text = ", ".join(metadata["genres"])
    
    # Описание
    if metadata.get("description"):
        SubElement(root, "Summary").text = metadata["description"]
    
    # Пометка, что это манга (важно для ридеров — они учитывают направление чтения)
    SubElement(root, "Manga").text = "YesAndRightToLeft"
    SubElement(root, "LanguageISO").text = "ru"
    
    xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode").encode("utf-8")
    return xml_bytes

async def download_cover(page, cover_url: str, output_dir: Path) -> bool:
    """Скачивает обложку манги и сохраняет как cover.jpg"""
    if not cover_url:
        return False
    
    cover_path = output_dir / "cover.jpg"
    if cover_path.exists():
        print(f"  ⏭️  Обложка уже скачана")
        return True
    
    try:
        response = await page.request.get(
            cover_url,
            headers={
                "Referer": "https://com-x.life/",
                "Accept": "image/*,*/*;q=0.8"
            }
        )
        if response.ok:
            cover_path.write_bytes(await response.body())
            print(f"  🖼️  Обложка сохранена: cover.jpg")
            return True
        else:
            print(f"  ⚠️  Не удалось скачать обложку: HTTP {response.status}")
            return False
    except Exception as e:
        print(f"  ⚠️  Ошибка скачивания обложки: {e}")
        return False

async def get_all_chapters(page, manga_url) -> dict[str, str]:
    """Заходит на страницу манги и собирает ВСЕ ссылки на главы со всех страниц пагинации"""
    print(f"📚 Сбор списка глав с: {manga_url}")
    await page.goto(manga_url, wait_until="domcontentloaded", timeout=60000)
    
    # Кликаем на вкладку с главами, если она есть и не активна
    try:
        tab = page.get_by_role("listitem").filter(has_text=re.compile(r"^Главы \(\d+\)$"))
        if await tab.count() > 0:
            await tab.click()
            await page.wait_for_timeout(1000) # Ждем прогрузки списка
    except Exception:
        print("  ⚠️  Не удалось кликнуть на вкладку глав, продолжаем...")

    last_page_btn = await page.locator(".cl__navigation > a", has_not_text="Вперед").last.click()

    all_chapters_data = []
    current_page = 1
    max_pages_safety = 100

    while current_page <= max_pages_safety:
        print(f"  📄 Сканирование страницы списка глав {current_page}...")
        await page.wait_for_timeout(1000)
        
        # Внутри родительского контейнера находим ссылку для URL и берем текстовое содержимое для названия.
        page_data = await page.locator(".cl__item").evaluate_all("""
            elements => elements.map(el => {
                const link = el.querySelector('a[href*="/reader/"]');
                
             let title = el.innerText
                .trim()
                .replace(/\\s+/g, ' ')    // Схлопываем все пробелы и переносы
                .replace(/^#\\d+\\s*/, '') // Убираем "#345 " в начале
                .replace(/\\d{2}\\.\\d{2}\\.\\d{4}/g, '');  // Убираем дату DD.MM.YYYY
                
                return {
                    title: title,
                    url: link ? link.href : null
                };
            }).filter(item => item.url)
        """)
        
        if not page_data:
            print("  ⚠️  Главы не найдены на этой странице. Возможно, селектор div.cl__item устарел.")
            break

        page_data.reverse()
        all_chapters_data.extend(page_data)
        print(f"    ✅ Найдено глав на этой странице: {len(page_data)}. Всего собрано: {len(all_chapters_data)}")
        
        # Ищем кнопку "Назад"
        next_btn = page.get_by_role("link", name="Назад").first
        
        if await next_btn.count() > 0:
            await next_btn.click()
            await page.wait_for_load_state("domcontentloaded")
            current_page += 1
        else:
            print("    🏁 Кнопка 'Назад' не найдена. Конец списка.")
            break

    # Убираем дубликаты (на случай, если сайт дублирует главы) и разворачиваем список (от старых к новым)
    unique_chapters = {}
    for item in all_chapters_data:
        if item['url'] not in unique_chapters:
            unique_chapters[item['url']] = item['title']

    # Переворачиваем обратно, чтобы ключом было название, а значением ссылка
    final_dict = {title: url for url, title in unique_chapters.items()}
    
    print(f"🎯 Итого собрано уникальных глав для скачивания: {len(final_dict)}")
    return final_dict

async def download_and_pack_chapter(page, chapter_url: str, chapter_name: str, 
                                     output_dir: Path, manga_name: str, 
                                     chapter_number: int, metadata: dict):
    """Скачивает главу и пакует в .cbz с метаданными"""
    cbz_filename = f"{chapter_name}.cbz"
    cbz_path = output_dir / cbz_filename
    
    if cbz_path.exists():
        print(f"  ⏭️  {chapter_name} уже скачан, пропускаем.")
        return

    print(f"  📖 Обработка: {chapter_name}")
    await page.goto(chapter_url, wait_until="domcontentloaded", timeout=60000)
    await close_popups(page)
    
    try:
        await page.locator("div.reader__item-wrap > img").first.wait_for(
            state="visible", timeout=15000
        )
    except Exception:
        print(f"  ❌ Таймаут ожидания картинок для {chapter_name}")
        return

    # Прокрутка для lazy-loading
    await page.evaluate("""
        async () => {
            const delay = ms => new Promise(r => setTimeout(r, ms));
            const height = document.body.scrollHeight;
            for (let i = 0; i < height; i += 500) {
                window.scrollTo(0, i);
                await delay(150);
            }
            window.scrollTo(0, height);
            await delay(1000);
            window.scrollTo(0, 0);
        }
    """)
    
    all_image_urls = await page.locator("div.reader__item-wrap > img").evaluate_all("""
        elements => elements
            .map(el => el.dataset.src || el.src)
            .filter(url => url && url.length > 10 && !url.startsWith('data:'))
    """)

    if not all_image_urls:
        print(f"  ❌ Не удалось найти URL картинок для {chapter_name}")
        return

    temp_dir = output_dir / f"temp_{chapter_name}"
    temp_dir.mkdir(exist_ok=True)
    
    # Параллельное скачивание
    semaphore = asyncio.Semaphore(2)
    pbar = tqdm(total=len(all_image_urls), desc=f"    {chapter_name[:30]}", unit="img", smoothing=0.1)
    
    async def download_one(idx: int, img_url: str):
        ext = img_url.split('.')[-1].split('?')[0] or 'jpg'
        filename = f"{idx + 1:03d}.{ext}"
        filepath = temp_dir / filename
        
        async with semaphore:
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # Случайная задержка перед запросом (имитация человека)
                    await asyncio.sleep(random.uniform(0.8, 2.0))
                    
                    response = await page.request.get(
                        img_url,
                        headers={
                            "Referer": chapter_url,
                            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                            # Добавляем заголовки, которые любит DDoS-Guard
                            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                            "Sec-Fetch-Dest": "image",
                            "Sec-Fetch-Mode": "no-cors"
                        },
                        timeout=60000 # Увеличиваем таймаут до 60 секунд на случай медленного ответа
                    )
                    
                    if response.ok:
                        filepath.write_bytes(await response.body())
                        pbar.update(1)
                        return True
                    else:
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2) # Пауза перед повторной попыткой
                        else:
                            print(f"\n    ❌ Ошибка {response.status} для {filename}")
                            return False
                            
                except Exception as e:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2)
                    else:
                        print(f"\n    ❌ Не удалось скачать {filename}: {e}")
                        return False
            
            return False
    
    tasks = [download_one(idx, url) for idx, url in enumerate(all_image_urls)]
    results = await asyncio.gather(*tasks)
    pbar.close()
    
    success_count = sum(1 for r in results if r)
    if success_count == 0:
        print(f"  ❌ Не удалось скачать ни одной картинки для {chapter_name}")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return

    # Упаковка с ComicInfo.xml
    print(f"    📦 Упаковка в {cbz_filename}...")
    clean_title = chapter_name.replace(f"{manga_name}_", "").replace("_", " ")
    
    with zipfile.ZipFile(cbz_path, 'w', zipfile.ZIP_STORED) as cbz:
        # !!! ИСПОЛЬЗУЕМ РЕАЛЬНЫЕ МЕТАДАННЫЕ !!!
        comicinfo = create_comicinfo_xml(manga_name, clean_title, chapter_number, metadata)
        cbz.writestr("ComicInfo.xml", comicinfo)
        
        for file in sorted(temp_dir.iterdir()):
            cbz.write(file, arcname=file.name)
            
    shutil.rmtree(temp_dir)
    print(f"  ✅ {chapter_name} успешно сохранен!")

def select_start_chapter(chapter_dict: dict[str, str]) -> dict[str, str]:
    """Показывает список глав и позволяет выбрать, с какой начать скачивание."""
    items = list(chapter_dict.items())
    total = len(items)
    
    print("\n" + "=" * 60)
    print(f"📚 Всего найдено глав: {total}")
    print("=" * 60)
    
    print("\n🔹 Первые главы:")
    for i in range(min(5, total)):
        print(f"   {i + 1:3d}. {items[i][0]}")
    
    if total > 10:
        print("\n🔹 Последние главы:")
        for i in range(max(5, total - 5), total):
            print(f"   {i + 1:3d}. {items[i][0]}")
    
    print("\n" + "-" * 60)
    
    while True:
        user_input = input(f"\n👉 Начать с главы (1-{total}, Enter=все): ").strip()
        
        if not user_input:
            print("✅ Скачиваем все главы")
            return chapter_dict
        
        try:
            start_num = int(user_input)
            if 1 <= start_num <= total:
                selected_items = items[start_num - 1:]
                selected_dict = dict(selected_items)
                
                # !!! НОВОЕ: показываем выбранную главу и спрашиваем подтверждение !!!
                print(f"\n📖 Выбрана глава: {selected_items[0][0]}")
                print(f"   Будет скачано: {len(selected_dict)} глав (с {start_num} по {total})")
                
                confirm = input("👉 Подтвердить выбор? (y/n, Enter=y): ").strip().lower()
                
                if confirm in ["", "y", "yes", "да", "д", "y "]:
                    print("✅ Подтверждено, начинаем скачивание")
                    return selected_dict
                else:
                    print("❌ Выбор отменён, введите заново")
                    continue
            else:
                print(f"❌ Число должно быть от 1 до {total}")
        except ValueError:
            print("❌ Введите число или оставьте пустым")

def process_url_string(text) -> str:
    # Убрали ^ в начале, теперь регулярка найдет подстроку внутри полной ссылки
    pattern = r"\d+-([a-zA-Z0-9]+)(?:-(.+))?-read-online\.html$"
    
    match = re.search(pattern, text)
    if match:
        main_word = match.group(1).capitalize()
        extra_words = match.group(2)
        
        if extra_words:
            extra_formatted = "_" + extra_words.replace("-", "_")
            return main_word + extra_formatted
        
        return main_word

    exit("Не удалось обработать название в ссылке")

async def main():
    manga_url = input("Вставьте ссылку главной страницы манги: ").strip()
    manga_name = process_url_string(manga_url)
    
    manga_dir = OUTPUT_DIR / manga_name
    manga_dir.mkdir(exist_ok=True)
    print(f"📁 Папка для сохранения: {manga_dir}")
    
    async with async_playwright() as p:
        browser, context = await setup_browser(p)
        page = await context.new_page()
        
        print("🚀 Запуск парсера...")
        
        # !!! НОВОЕ: собираем метаданные ПЕРЕД сбором глав !!!
        metadata = await get_manga_metadata(page, manga_url)
        
        # Скачиваем обложку один раз
        await download_cover(page, metadata.get("cover_url", ""), manga_dir)
        
        # Собираем список глав
        chapter_dict = await get_all_chapters(page, manga_url)
        
        # Интерактивный выбор стартовой главы
        chapters_to_download = select_start_chapter(chapter_dict)
        
        # Скачиваем главы
        total = len(chapters_to_download)
        for idx, (title, url) in enumerate(chapters_to_download.items(), start=1):
            clean_title = title.replace('/', '_').replace(':', '-').replace('\\', '_')
            chapter_name = f"{clean_title}"
            
            print(f"\n[{idx}/{total}] ", end="")
            
            try:
                await download_and_pack_chapter(
                    page, url, chapter_name, manga_dir,
                    manga_name=manga_name,
                    chapter_number=idx,
                    metadata=metadata  # ← передаём метаданные
                )
            except Exception as e:
                print(f"  ❌ Критическая ошибка на главе {chapter_name}: {e}")
                
        await browser.close()
        print(f"\n🎉 Все операции завершены! Манга ждет тебя в папке {manga_dir}/")

if __name__ == '__main__':
    asyncio.run(main())
