import asyncio
import random
import re
import shutil
import zipfile
from pathlib import Path

from playwright.async_api import Page
from playwright_stealth import Stealth
from tqdm import tqdm

import config
from utils import create_comicinfo_xml


class MangaScraper:
    def __init__(self, manga_url: str):
        self.manga_url = manga_url
        self.manga_name = self._extract_manga_name(manga_url)
        self.output_dir = config.BASE_OUTPUT_DIR / self.manga_name
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Кэшируемые данные
        self.metadata = {}
        self.cached_chapters = {}
        self.failed_chapters = []

        # Файлы для кэширования
        self._metadata_file = self.output_dir / "metadata.json"
        self._chapters_file = self.output_dir / "chapters.json"

    def save_chapters_cache(self):
        """Сохраняет список глав в JSON"""
        import json

        with open(self._chapters_file, "w", encoding="utf-8") as f:
            json.dump(self.cached_chapters, f, ensure_ascii=False, indent=2)
        print(f"  💾 Список глав сохранён в {self._chapters_file.name}")

    def load_chapters_cache(self) -> bool:
        """Загружает список глав из JSON. Возвращает True, если успешно."""
        import json

        if not self._chapters_file.exists():
            return False
        try:
            with open(self._chapters_file, "r", encoding="utf-8") as f:
                self.cached_chapters = json.load(f)
            print(f"  📂 Загружено {len(self.cached_chapters)} глав из кэша")
            return True
        except Exception as e:
            print(f"  ⚠️  Не удалось загрузить кэш глав: {e}")
            return False

    def _extract_manga_name(self, url: str) -> str:
        match = re.search(r"\d+-([a-zA-Z0-9]+)(?:-(.+))?-read-online\.html$", url)
        if match:
            main = match.group(1).capitalize()
            extra = match.group(2)
            return main + (extra.replace("-", "_") if extra else "")
        return "UnknownManga"

    async def setup_browser(self, p):
        browser = await p.firefox.launch(headless=False)  # В продакшене headless=True
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )
        stealth = Stealth(
            navigator_languages_override=("ru-RU", "ru"), init_scripts_only=True
        )

        # Автоматическое закрытие через init_script
        await context.add_init_script("""
            let popupClosed = false;
            
            function closePopupOnce() {
                if (popupClosed) return; // Уже закрыли, не повторяем
                
                // Ищем кнопку закрытия по классу un-x
                const closeBtn = document.querySelector('.un-x');
                if (closeBtn) {
                    closeBtn.click();
                    popupClosed = true;
                    console.log('[Scraper] ✅ Popup закрыт (кнопка .un-x)');
                    return;
                }
                
                // Фоллбэк: ищем по тексту "Закрыть"
                const buttons = document.querySelectorAll('button, a, span');
                for (const btn of buttons) {
                    if (btn.innerText.trim() === 'Закрыть') {
                        btn.click();
                        popupClosed = true;
                        console.log('[Scraper] ✅ Popup закрыт (текст "Закрыть")');
                        return;
                    }
                }
            }
            
            // Проверяем каждые 500мс, но только первые 10 секунд
            let attempts = 0;
            const interval = setInterval(() => {
                closePopupOnce();
                attempts++;
                if (popupClosed || attempts > 20) {
                    clearInterval(interval);
                }
            }, 500);
        """)

        await stealth.apply_stealth_async(context)
        return browser, context

    async def download_cover(self, page, cover_url: str, output_dir: Path) -> bool:
        """Скачивает обложку манги и сохраняет как cover.jpg"""
        if not cover_url:
            print("  ⚠️  URL обложки не найден")
            return False

        cover_path = output_dir / "cover.jpg"
        if cover_path.exists():
            print("  ⏭️  Обложка уже скачана")
            return True

        print("  📥 Скачивание обложки...")
        try:
            # Используем тот же надежный метод с Referer и увеличенным таймаутом
            response = await page.request.get(
                cover_url,
                headers={
                    "Referer": "https://com-x.life/",
                    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                },
                timeout=config.IMAGE_DOWNLOAD_TIMEOUT,
            )
            if response.ok:
                cover_path.write_bytes(await response.body())
                print("  🖼️  Обложка успешно сохранена: cover.jpg")
                return True
            else:
                print(f"  ⚠️  Не удалось скачать обложку: HTTP {response.status}")
                return False
        except Exception as e:
            print(f"  ⚠️  Ошибка скачивания обложки: {e}")
            return False

    async def get_metadata(self, page: Page, manga_url: str) -> dict:
        """Парсит метаданные манги (автор, издатель, жанры, обложка) с главной страницы"""
        print("📋 Сбор метаданных манги...")

        await page.goto(
            manga_url, wait_until="domcontentloaded", timeout=config.NETWORK_IDLE
        )

        try:
            # Ждём появления списка с информацией (Автор, Год и т.д.)
            await page.locator("ul.page__list").first.wait_for(
                state="visible", timeout=10000
            )
            print("  ✅ Блок метаданных загружен")
        except Exception:
            print("  ⚠️  Блок метаданных не загрузился, продолжаем...")

        # Ждём загрузки тегов
        try:
            await page.locator("div.page__tags").first.wait_for(
                state="visible", timeout=5000
            )
        except Exception:
            pass

        self.metadata = {
            "author": "",
            "artist": "",
            "publisher": "",
            "year": "",
            "status": "",
            "genres": [],
            "cover_url": "",
            "description": "",
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
            item_type = item["type"]
            value = item["value"]

            # Сопоставляем типы (на случай разных написаний)
            if "автор" in item_type or "author" in item_type:
                self.metadata["author"] = value
            elif (
                "художник" in item_type
                or "artist" in item_type
                or "иллюстратор" in item_type
            ):
                self.metadata["artist"] = value
            elif "издатель" in item_type or "publisher" in item_type:
                self.metadata["publisher"] = value
            elif "год" in item_type or "year" in item_type:
                self.metadata["year"] = value
            elif "статус" in item_type or "status" in item_type:
                self.metadata["status"] = value

        # Если художник не указан отдельно, используем автора
        if not self.metadata["artist"]:
            self.metadata["artist"] = self.metadata["author"]

        print(f"    ✍️  Автор: {self.metadata['author']}")
        print(f"    🎨 Художник: {self.metadata['artist']}")
        print(f"    🏢 Издатель: {self.metadata['publisher']}")
        print(f"    📅 Год: {self.metadata['year']}")
        print(f"    📊 Статус: {self.metadata['status']}")

        # 2. Парсим жанры/теги
        genres = await page.locator("div.page__tags > a").evaluate_all("""
            elements => elements.map(el => el.innerText.trim()).filter(t => t.length > 0)
        """)
        self.metadata["genres"] = genres
        print(
            f"    🏷️  Жанры: {', '.join(genres[:5])}{'...' if len(genres) > 5 else ''}"
        )

        # 3. Парсим URL обложки
        cover_url = await page.locator(
            "img.manga-cover, .page__poster img, .poster img, img[src*='uploads/posts']"
        ).first.get_attribute("src")
        if cover_url:
            # Если URL относительный, делаем его абсолютным
            if cover_url.startswith("/"):
                from urllib.parse import urljoin

                cover_url = urljoin(manga_url, cover_url)
            self.metadata["cover_url"] = cover_url
            print(f"    🖼️  Обложка: {cover_url}")

        # 4. Парсим описание
        try:
            description = await page.locator(
                "div.page__text, .page__description, .description"
            ).first.inner_text(timeout=3000)
            self.metadata["description"] = (
                description.strip()
            )  # [:500]  # Обрезаем до 500 символов для XML
        except Exception:
            self.metadata["description"] = ""

        return self.metadata

    async def get_all_chapters(self, page: Page, manga_url: str) -> dict[str, str]:
        """Заходит на страницу манги и собирает ВСЕ ссылки на главы со всех страниц пагинации"""
        print(f"📚 Сбор списка глав с: {manga_url}")
        await page.goto(
            manga_url, wait_until="domcontentloaded", timeout=config.NETWORK_IDLE
        )

        # Кликаем на вкладку с главами, если она есть и не активна
        try:
            await (
                page.get_by_role("listitem")
                .filter(has_text=re.compile(r"^Главы \(\d+\)$"))
                .wait_for(state="visible", timeout=10000)
            )

            tab = page.get_by_role("listitem").filter(
                has_text=re.compile(r"^Главы \(\d+\)$")
            )

            if await tab.count() > 0:
                await tab.wait_for(state="visible", timeout=5000)
                await tab.click()
                await page.wait_for_timeout(1000)  # Ждем прогрузки списка
        except Exception:
            print("  ⚠️  Не удалось кликнуть на вкладку глав, продолжаем...")

        await page.locator(".cl__navigation > a", has_not_text="Вперед").last.click()

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
                print(
                    "  ⚠️  Главы не найдены на этой странице. Возможно, селектор div.cl__item устарел."
                )
                break

            page_data.reverse()
            all_chapters_data.extend(page_data)
            print(
                f"    ✅ Найдено глав на этой странице: {len(page_data)}. Всего собрано: {len(all_chapters_data)}"
            )

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
            if item["url"] not in unique_chapters:
                unique_chapters[item["url"]] = item["title"]

        # Переворачиваем обратно, чтобы ключом было название, а значением ссылка
        final_dict = {title: url for url, title in unique_chapters.items()}

        print(f"🎯 Итого собрано уникальных глав для скачивания: {len(final_dict)}")
        return final_dict

    async def download_chapter(
        self, page, url: str, chapter_name: str, chapter_number: int
    ) -> bool:
        """Скачивает главу и пакует в .cbz"""
        cbz_filename = f"{chapter_name}.cbz"
        cbz_path = self.output_dir / cbz_filename

        if cbz_path.exists():
            file_size = cbz_path.stat().st_size
            print(
                f"  ⏭️  {cbz_filename} уже существует ({file_size / 1024:.1f} KB), пропускаем"
            )
            return True

        print(f"  📖 Обработка: {chapter_name}")
        print(f"     🔗 URL: {url}")  # Отладочный вывод

        await page.goto(url, wait_until="networkidle", timeout=config.NETWORK_IDLE)

        # Принудительно ждём появления картинок
        try:
            await page.locator("div.reader__item-wrap > img").first.wait_for(
                state="visible", timeout=15000
            )
        except Exception:
            print("  ❌ Таймаут ожидания картинок")
            return False

        # Даём время на полную загрузку lazy-loaded картинок
        await page.wait_for_timeout(2000)

        # Прокрутка для lazy-loading
        await page.evaluate("""async () => {
            const delay = ms => new Promise(r => setTimeout(r, ms));
            const height = document.body.scrollHeight;
            for (let i = 0; i < height; i += 500) { window.scrollTo(0, i); await delay(150); }
            window.scrollTo(0, height); await delay(1000); window.scrollTo(0, 0);
        }""")

        # Собираем URL картинок
        all_image_urls = await page.locator("div.reader__item-wrap > img").evaluate_all("""
            elements => elements.map(el => el.dataset.src || el.src).filter(url => url && url.length > 10 && !url.startsWith('data:'))
        """)

        if not all_image_urls:
            print("  ❌ Не удалось найти URL картинок")
            return False

        # Отладочный вывод первых 3 URL
        print("    🔍 Первые URL картинок:")
        for i, img_url in enumerate(all_image_urls[:3]):
            print(f"       {i + 1}. {img_url}")

        total_images = len(all_image_urls)
        print(f"    ⬇️  Скачивание {total_images} изображений...")
        temp_dir = self.output_dir / f"temp_{chapter_name}"
        temp_dir.mkdir(exist_ok=True)

        semaphore = asyncio.Semaphore(config.MAX_PARALLEL_DOWNLOADS)
        pbar = tqdm(
            total=total_images,
            desc=f"    {chapter_name[:30]}",
            unit="img",
            smoothing=0.1,
        )
        success_count = 0

        async def download_one(idx: int, img_url: str):
            nonlocal success_count
            ext = img_url.split(".")[-1].split("?")[0] or "jpg"
            filepath = temp_dir / f"{idx + 1:03d}.{ext}"

            async with semaphore:
                for attempt in range(config.MAX_RETRIES_PER_IMAGE):
                    try:
                        await asyncio.sleep(random.uniform(0.8, 2.0))
                        response = await page.request.get(
                            img_url,
                            headers={
                                "Referer": url,
                                "Accept": "image/webp,image/*,*/*;q=0.8",
                            },
                            timeout=config.IMAGE_DOWNLOAD_TIMEOUT,
                        )

                        if response.ok:
                            filepath.write_bytes(await response.body())
                            success_count += 1
                            pbar.update(1)
                            return True
                        await asyncio.sleep(2)
                    except Exception:
                        await asyncio.sleep(2)
            return False

        tasks = [download_one(idx, url) for idx, url in enumerate(all_image_urls)]
        await asyncio.gather(*tasks)
        pbar.close()

        if success_count != total_images:
            print(f"\n  ⚠️  Скачано {success_count} из {total_images}. Глава неполная!")
            print(f"  🧊 Кулдаун {config.COOLDOWN_ON_FAILURE_SECONDS} сек...")
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.failed_chapters.append(chapter_name)
            await asyncio.sleep(config.COOLDOWN_ON_FAILURE_SECONDS)
            return False

        print(f"    📦 Упаковка в {cbz_filename}...")
        clean_title = chapter_name.replace(f"{self.manga_name}_", "").replace("_", " ")
        with zipfile.ZipFile(cbz_path, "w", zipfile.ZIP_STORED) as cbz:
            cbz.writestr(
                "ComicInfo.xml",
                create_comicinfo_xml(
                    self.manga_name, clean_title, chapter_number, self.metadata
                ),
            )
            for file in sorted(temp_dir.iterdir()):
                cbz.write(file, arcname=file.name)

        shutil.rmtree(temp_dir)
        print(f"  ✅ {chapter_name} сохранен!")

        # Задержка между главами
        await asyncio.sleep(1.5)

        return True
