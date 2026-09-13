"""
website_tester.py
==================
Универсальный инструмент для автоматизированного тестирования веб-сайтов.
Построен на Playwright (async API).

Что делает:
    1. Smoke-тест       — доступность сайта (код 200) и скорость загрузки
    2. UI/UX-тест       — скриншоты в разных разрешениях, битые ссылки, битые картинки
    3. Тест форм        — автозаполнение тестовыми данными (позитивные и негативные сценарии)
    4. Отчётность       — HTML-отчёт + текстовый лог с деталями каждой проверки

ВАЖНО — про этичность автоматизированного тестирования:
    Направляй этот инструмент только на сайты, которыми владеешь сам, или на
    которые есть явное разрешение владельца тестировать. Автоматические
    запросы к чужому сайту (особенно тест форм "плохими" данными) без
    разрешения — это не то же самое, что открыть сайт в браузере руками.
    Настройки по умолчанию в config.py указывают на публичные учебные сайты,
    специально созданные для практики автоматизации.

Установка:
    pip install playwright
    playwright install chromium

Запуск:
    python website_tester.py

Настройка списка сайтов и параметров — в файле config.py рядом.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright, Page, BrowserContext, Response

import config


# ============================================================================
# СТРУКТУРЫ ДАННЫХ ДЛЯ РЕЗУЛЬТАТОВ
# ============================================================================
# Каждая проверка (smoke-тест, битая ссылка, тест формы и т.д.) превращается
# в один объект CheckResult — это удобно, чтобы потом единообразно собрать
# все результаты в отчёт, независимо от того, какой именно тест их породил.

@dataclass
class CheckResult:
    """Результат одной конкретной проверки."""
    category: str          # "Smoke-тест", "Битые ссылки", "Формы" и т.д.
    url: str                # адрес, к которому относится проверка
    name: str               # короткое название проверки
    passed: bool             # True = проверка пройдена, False = найдена проблема
    details: str = ""       # подробности (текст ошибки, статус-код и т.п.)
    screenshot_file: str | None = None  # имя файла скриншота (relative), если есть
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


class Reporter:
    """
    Собирает результаты всех проверок по ходу работы программы и в конце
    умеет сохранить их в двух видах: простой текстовый лог (быстро читать
    построчно) и HTML-отчёт (удобно смотреть в браузере, с цветовой
    индикацией и скриншотами).
    """

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.results: list[CheckResult] = []
        self.run_started_at = datetime.now()

    def add(self, result: CheckResult) -> None:
        """Добавляет один результат в общий список и сразу печатает его в консоль —
        так видно прогресс работы в реальном времени, не дожидаясь конца."""
        self.results.append(result)
        status_icon = "✅" if result.passed else "❌"
        print(f"{status_icon} [{result.category}] {result.name} — {result.url}")
        if result.details:
            print(f"    {result.details}")

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed_count(self) -> int:
        return self.total - self.passed_count

    def save_text_log(self) -> Path:
        """Сохраняет простой текстовый лог — по одной строке на проверку."""
        log_path = self.output_dir / "report.txt"
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"Отчёт о тестировании — {self.run_started_at.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Всего проверок: {self.total} | Успешно: {self.passed_count} | Ошибок: {self.failed_count}\n")
            f.write("=" * 70 + "\n\n")
            for r in self.results:
                status = "OK" if r.passed else "FAIL"
                f.write(f"[{r.timestamp}] [{status}] [{r.category}] {r.name}\n")
                f.write(f"    URL: {r.url}\n")
                if r.details:
                    f.write(f"    Детали: {r.details}\n")
                f.write("\n")
        return log_path

    def save_html_report(self) -> Path:
        """Сохраняет наглядный HTML-отчёт — открывается в любом браузере."""
        html_path = self.output_dir / "report.html"

        # Группируем результаты по URL, чтобы отчёт читался как "по каждому сайту — что нашли"
        by_url: dict[str, list[CheckResult]] = {}
        for r in self.results:
            by_url.setdefault(r.url, []).append(r)

        rows_html = ""
        for url, results in by_url.items():
            url_passed = sum(1 for r in results if r.passed)
            url_total = len(results)
            rows_html += f'<h2 class="url-heading">{escape_html(url)} <span class="url-score">{url_passed}/{url_total}</span></h2>\n'
            rows_html += '<table class="results-table">\n'
            for r in results:
                css_class = "row-pass" if r.passed else "row-fail"
                icon = "✅" if r.passed else "❌"
                screenshot_html = ""
                if r.screenshot_file:
                    screenshot_html = f'<br><a href="{escape_html(r.screenshot_file)}" target="_blank"><img class="thumb" src="{escape_html(r.screenshot_file)}" alt="скриншот"></a>'
                rows_html += f"""
                <tr class="{css_class}">
                    <td class="cell-icon">{icon}</td>
                    <td class="cell-category">{escape_html(r.category)}</td>
                    <td class="cell-name">{escape_html(r.name)}</td>
                    <td class="cell-details">{escape_html(r.details)}{screenshot_html}</td>
                    <td class="cell-time">{r.timestamp}</td>
                </tr>
                """
            rows_html += "</table>\n"

        html = HTML_REPORT_TEMPLATE.format(
            generated_at=self.run_started_at.strftime("%Y-%m-%d %H:%M:%S"),
            total=self.total,
            passed=self.passed_count,
            failed=self.failed_count,
            pass_pct=round(100 * self.passed_count / self.total) if self.total else 0,
            rows_html=rows_html,
        )
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        return html_path


def escape_html(text: str) -> str:
    """Экранирует спецсимволы, чтобы содержимое (в том числе то, что мы сами
    вводили в формы вроде <script>) не сломало вёрстку самого HTML-отчёта."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


HTML_REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Отчёт о тестировании</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; background:#12161b; color:#eae6df; margin:0; padding:24px; }}
  .container {{ max-width: 960px; margin: 0 auto; }}
  h1 {{ font-size: 24px; margin-bottom:4px; }}
  .meta {{ color:#8b93a1; font-size:13px; margin-bottom:20px; }}
  .summary {{ display:flex; gap:12px; margin-bottom:28px; }}
  .summary-card {{ background:#1b2129; border:1px solid #2b3340; border-radius:10px; padding:14px 18px; flex:1; }}
  .summary-card .num {{ font-size:26px; font-weight:700; }}
  .summary-card .label {{ font-size:12px; color:#8b93a1; text-transform:uppercase; letter-spacing:.05em; }}
  .summary-card.pass .num {{ color:#5fb8a8; }}
  .summary-card.fail .num {{ color:#c65c4a; }}
  .url-heading {{ font-size:16px; margin-top:28px; border-bottom:1px solid #2b3340; padding-bottom:8px; display:flex; justify-content:space-between; align-items:center; }}
  .url-score {{ font-size:13px; color:#8b93a1; font-weight:normal; }}
  table.results-table {{ width:100%; border-collapse:collapse; margin-top:10px; font-size:13px; }}
  .results-table td {{ padding:8px 10px; border-bottom:1px solid #232b34; vertical-align:top; }}
  .row-fail {{ background:rgba(198,92,74,0.08); }}
  .row-pass {{ background:transparent; }}
  .cell-icon {{ width:28px; text-align:center; }}
  .cell-category {{ width:120px; color:#c98a4b; white-space:nowrap; }}
  .cell-name {{ width:220px; font-weight:600; }}
  .cell-details {{ color:#8b93a1; word-break:break-word; }}
  .cell-time {{ width:130px; color:#8b93a1; font-family:monospace; font-size:11px; white-space:nowrap; }}
  .thumb {{ max-width:220px; border-radius:6px; border:1px solid #2b3340; margin-top:6px; display:block; }}
</style>
</head>
<body>
<div class="container">
  <h1>Отчёт о тестировании сайтов</h1>
  <div class="meta">Сформирован: {generated_at}</div>
  <div class="summary">
    <div class="summary-card"><div class="num">{total}</div><div class="label">Всего проверок</div></div>
    <div class="summary-card pass"><div class="num">{passed}</div><div class="label">Успешно</div></div>
    <div class="summary-card fail"><div class="num">{failed}</div><div class="label">Ошибок</div></div>
    <div class="summary-card"><div class="num">{pass_pct}%</div><div class="label">Общий балл</div></div>
  </div>
  {rows_html}
</div>
</body>
</html>
"""


# ============================================================================
# 1. SMOKE-ТЕСТ — доступность сайта и скорость загрузки
# ============================================================================

async def run_smoke_test(page: Page, url: str, reporter: Reporter) -> bool:
    """
    Открывает страницу и проверяет два самых базовых, но самых важных факта:
    - сервер ответил кодом 200 (страница вообще существует и отдаётся)
    - страница загрузилась быстрее порога из config.MAX_LOAD_TIME_SECONDS

    Возвращает True, если сайт "жив" и можно продолжать остальные проверки
    для него — если сайт недоступен, нет смысла пытаться искать на нём
    битые ссылки или тестировать формы.
    """
    start_time = time.monotonic()
    try:
        response: Response | None = await page.goto(
            url, timeout=config.PAGE_TIMEOUT_MS, wait_until="load"
        )
    except Exception as e:
        # Любая сетевая ошибка (сайт не отвечает, неверный адрес и т.п.)
        reporter.add(CheckResult(
            category="Smoke-тест", url=url, name="Доступность сайта",
            passed=False, details=f"Не удалось открыть страницу: {e}",
        ))
        return False

    load_time = time.monotonic() - start_time

    # Проверка кода ответа
    status_ok = response is not None and response.status == 200
    reporter.add(CheckResult(
        category="Smoke-тест", url=url, name="Код ответа сервера",
        passed=status_ok,
        details=f"Получен код: {response.status if response else 'нет ответа'}",
    ))

    # Проверка скорости загрузки
    speed_ok = load_time <= config.MAX_LOAD_TIME_SECONDS
    reporter.add(CheckResult(
        category="Smoke-тест", url=url, name="Скорость загрузки",
        passed=speed_ok,
        details=f"Загрузилась за {load_time:.2f}с (порог: {config.MAX_LOAD_TIME_SECONDS}с)",
    ))

    return status_ok


# ============================================================================
# 2. UI/UX-ТЕСТИРОВАНИЕ — скриншоты, битые ссылки, битые картинки
# ============================================================================

async def take_responsive_screenshots(
    context: BrowserContext, url: str, reporter: Reporter, screenshots_dir: Path
) -> None:
    """
    Открывает страницу в нескольких разрешениях экрана (мобильный/планшет/
    десктоп — список берётся из config.VIEWPORTS), сохраняет скриншот
    каждого и проверяет горизонтальный overflow (когда контент шире экрана
    и появляется нежелательная горизонтальная прокрутка — частая проблема
    адаптивной вёрстки, особенно на мобильных).

    Скриншоты сняты с full_page=True — это значит, что снимается ВСЯ высота
    страницы, а если у страницы есть горизонтальный overflow, ширина
    скриншота тоже будет больше заявленного viewport. Это не баг съёмки, а
    отражение реальной проблемы страницы — см. проверку overflow ниже.
    """
    for viewport in config.VIEWPORTS:
        page = await context.new_page()
        await page.set_viewport_size({"width": viewport["width"], "height": viewport["height"]})
        try:
            await page.goto(url, timeout=config.PAGE_TIMEOUT_MS, wait_until="load")

            # Проверка горизонтального overflow: если реальная ширина контента
            # (scrollWidth) заметно больше ширины видимой области (clientWidth),
            # значит на странице появляется нежелательная горизонтальная
            # прокрутка при этом разрешении экрана.
            scroll_width = await page.evaluate("document.documentElement.scrollWidth")
            client_width = await page.evaluate("document.documentElement.clientWidth")
            overflow_px = scroll_width - client_width
            no_overflow = overflow_px <= 5  # небольшой допуск на погрешность рендеринга
            reporter.add(CheckResult(
                category="UI/UX", url=url,
                name=f"Горизонтальный overflow ({viewport['name']}, {viewport['width']}px)",
                passed=no_overflow,
                details=(
                    "Overflow не обнаружен" if no_overflow else
                    f"Контент шире экрана на {overflow_px}px (scrollWidth={scroll_width}, viewport={client_width}) — "
                    f"вероятна горизонтальная прокрутка на этом разрешении"
                ),
            ))

            # безопасное имя файла из адреса сайта
            safe_name = urlparse(url).netloc.replace(".", "_")
            filename = f"{safe_name}_{viewport['name']}.png"
            screenshot_path = screenshots_dir / filename
            await page.screenshot(path=str(screenshot_path), full_page=True)
            reporter.add(CheckResult(
                category="UI/UX", url=url,
                name=f"Скриншот ({viewport['name']}, {viewport['width']}×{viewport['height']})",
                passed=True, details=f"Сохранено: {screenshot_path.name}",
                screenshot_file=f"screenshots/{screenshot_path.name}",
            ))
        except Exception as e:
            reporter.add(CheckResult(
                category="UI/UX", url=url,
                name=f"Скриншот ({viewport['name']})",
                passed=False, details=f"Не удалось снять скриншот: {e}",
            ))
        finally:
            await page.close()


async def check_broken_links(page: Page, url: str, reporter: Reporter) -> None:
    """
    Собирает все ссылки (<a href="...">) на странице и проверяет каждую
    лёгким HTTP-запросом (без полной загрузки страницы в браузере — так
    быстрее, чем открывать каждую ссылку как отдельную вкладку).

    Проверяются только "настоящие" ссылки — javascript:, mailto:, tel: и
    пустые/якорные (#section) пропускаются, так как это не переходы на
    другую страницу.
    """
    hrefs: list[str] = await page.eval_on_selector_all(
        "a[href]", "elements => elements.map(el => el.getAttribute('href'))"
    )

    checked: set[str] = set()  # чтобы не проверять один и тот же адрес дважды
    for href in hrefs:
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue

        absolute_url = urljoin(url, href)
        if absolute_url in checked:
            continue
        checked.add(absolute_url)

        try:
            # page.request — это лёгкий HTTP-клиент Playwright, без открытия
            # реальной вкладки браузера. Так проверка ссылок идёт быстро.
            response = await page.request.get(absolute_url, timeout=config.PAGE_TIMEOUT_MS)
            link_ok = response.status < 400
            reporter.add(CheckResult(
                category="Битые ссылки", url=url, name=absolute_url,
                passed=link_ok, details=f"Код ответа: {response.status}",
            ))
        except Exception as e:
            reporter.add(CheckResult(
                category="Битые ссылки", url=url, name=absolute_url,
                passed=False, details=f"Ссылка недоступна: {e}",
            ))


async def check_broken_images(page: Page, url: str, reporter: Reporter) -> None:
    """
    Проверяет все <img> на странице через сам браузер: если картинка не
    загрузилась (неверный путь, сервер отдал ошибку и т.п.), у неё
    naturalWidth будет равен 0 — это и есть надёжный признак "битой" картинки,
    который понимает сам браузер, без необходимости отдельно запрашивать
    каждую картинку по сети.
    """
    broken_images: list[dict] = await page.eval_on_selector_all(
        "img",
        """elements => elements
            .filter(img => img.complete && img.naturalWidth === 0)
            .map(img => ({ src: img.src || img.getAttribute('src') || '(без src)' }))
        """,
    )
    total_images: int = await page.eval_on_selector_all("img", "elements => elements.length")

    if total_images == 0:
        reporter.add(CheckResult(
            category="Битые картинки", url=url, name="Картинки на странице",
            passed=True, details="На странице нет тегов <img>",
        ))
        return

    if not broken_images:
        reporter.add(CheckResult(
            category="Битые картинки", url=url, name="Все картинки загрузились",
            passed=True, details=f"Проверено картинок: {total_images}",
        ))
    else:
        for img in broken_images:
            reporter.add(CheckResult(
                category="Битые картинки", url=url, name=img["src"],
                passed=False, details="Картинка не загрузилась (naturalWidth = 0)",
            ))


# ============================================================================
# 3. ТЕСТИРОВАНИЕ ФОРМ
# ============================================================================
#
# ВАЖНОЕ РЕШЕНИЕ ПО ДИЗАЙНУ: по умолчанию формы заполняются, но НЕ
# отправляются по-настоящему (кнопка Submit не нажимается). Вместо этого
# используется встроенная в браузер проверка form.checkValidity() — это
# честная, широко используемая в реальном QA техника: она показывает,
# заблокирует ли браузер отправку невалидных данных, без риска реально
# отправить тестовые данные на сервер чужого сайта (спам в реальную почту,
# создание мусорных заявок и т.п.).
#
# Если тестируешь СВОЙ сайт (или тестовую среду) и хочешь проверить
# серверную обработку — включи config.ACTUALLY_SUBMIT_FORMS = True.
# На чужих сайтах без разрешения владельца этого делать не стоит.

async def get_field_category(field) -> str:
    """Пытается угадать смысл поля формы (email/телефон/сообщение/имя/прочее)
    по его type, name и id — простая, но рабочая для большинства форм эвристика."""
    field_type = (await field.get_attribute("type")) or "text"
    tag_name = (await field.evaluate("el => el.tagName.toLowerCase()"))
    name_attr = (await field.get_attribute("name")) or ""
    id_attr = (await field.get_attribute("id")) or ""
    placeholder = (await field.get_attribute("placeholder")) or ""
    combined = f"{name_attr} {id_attr} {placeholder}".lower()

    if field_type == "email" or "mail" in combined:
        return "email"
    if field_type == "tel" or "phone" in combined or "тел" in combined:
        return "phone"
    if tag_name == "textarea" or "message" in combined or "comment" in combined or "сообщен" in combined:
        return "message"
    if "name" in combined or "имя" in combined:
        return "name"
    return "default"


async def fill_form_with_scenario(form, values: dict) -> int:
    """Заполняет все подходящие поля формы значениями из сценария.
    Возвращает количество реально заполненных полей (пригодится, чтобы
    пропускать формы, где заполнять было вообще нечего)."""
    fields = await form.query_selector_all("input, textarea")
    filled_count = 0
    for field in fields:
        field_type = (await field.get_attribute("type")) or "text"
        # Эти типы полей не заполняются текстом — пропускаем их
        if field_type in ("submit", "button", "checkbox", "radio", "hidden", "file", "image", "reset"):
            continue
        is_disabled = await field.is_disabled()
        if is_disabled:
            continue

        category = await get_field_category(field)
        value = values.get(category, values.get("default", ""))
        try:
            await field.fill(value)
            filled_count += 1
        except Exception:
            # Некоторые поля невозможно заполнить обычным fill() (например,
            # с маской ввода) — пропускаем, не прерывая тест всей формы
            continue
    return filled_count


async def test_forms(page: Page, url: str, reporter: Reporter) -> None:
    """Находит все формы на странице и прогоняет каждую через набор
    сценариев из config.FORM_TEST_SCENARIOS (позитивный + несколько
    негативных)."""
    forms = await page.query_selector_all("form")

    if not forms:
        reporter.add(CheckResult(
            category="Формы", url=url, name="Формы на странице",
            passed=True, details="Форм на странице не найдено — тест форм пропущен",
        ))
        return

    for form_index, form in enumerate(forms, start=1):
        form_label = f"Форма #{form_index}"

        for scenario in config.FORM_TEST_SCENARIOS:
            try:
                filled = await fill_form_with_scenario(form, scenario["values"])
                if filled == 0:
                    continue  # в форме нет полей, которые можно было бы осмысленно заполнить

                # checkValidity() — встроенная в браузер проверка HTML5-валидации
                # (required, type="email", pattern и т.п.) без реальной отправки
                is_valid = await form.evaluate("f => f.checkValidity()")

                expect_rejected = scenario["expect_rejected"]
                if expect_rejected is True:
                    passed = not is_valid
                    details = (
                        "Браузер корректно отклонил невалидные/пустые данные"
                        if passed else
                        "⚠ Форма приняла невалидные или пустые данные без возражений — стоит добавить валидацию"
                    )
                elif expect_rejected is False:
                    passed = is_valid
                    details = (
                        "Корректные данные приняты валидацией"
                        if passed else
                        "Форма отклонила полностью корректные данные — возможная ошибка в валидации"
                    )
                else:
                    # Сценарий без однозначно "правильного" исхода (спецсимволы) —
                    # просто фиксируем факт в отчёте для ручного просмотра
                    passed = True
                    details = f"checkValidity() = {is_valid} (зафиксировано для ручной проверки, авто-оценка не применяется)"

                reporter.add(CheckResult(
                    category="Формы", url=url,
                    name=f"{form_label} — {scenario['name']}",
                    passed=passed, details=details,
                ))

                # Необязательная реальная отправка — только если явно включено
                # в конфиге, и только имеет смысл для позитивного сценария
                if getattr(config, "ACTUALLY_SUBMIT_FORMS", False) and expect_rejected is False:
                    await submit_form_and_check(page, form, url, form_label, reporter)

            except Exception as e:
                reporter.add(CheckResult(
                    category="Формы", url=url,
                    name=f"{form_label} — {scenario['name']}",
                    passed=False, details=f"Ошибка при тестировании формы: {e}",
                ))


async def submit_form_and_check(page: Page, form, url: str, form_label: str, reporter: Reporter) -> None:
    """Реально нажимает кнопку отправки формы и проверяет, что произошло
    (переход на другую страницу обычно означает успешную отправку).
    Вызывается ТОЛЬКО если config.ACTUALLY_SUBMIT_FORMS = True."""
    submit_btn = await form.query_selector('button[type="submit"], input[type="submit"], button:not([type])')
    if not submit_btn:
        return
    url_before = page.url
    try:
        async with page.expect_navigation(timeout=5000):
            await submit_btn.click()
        navigated = page.url != url_before
        reporter.add(CheckResult(
            category="Формы (реальная отправка)", url=url, name=f"{form_label} — переход после отправки",
            passed=navigated, details=f"URL после отправки: {page.url}",
        ))
    except Exception:
        # Не произошло навигации за 5 секунд — форма могла обработаться через AJAX,
        # это не обязательно ошибка, просто фиксируем факт
        reporter.add(CheckResult(
            category="Формы (реальная отправка)", url=url, name=f"{form_label} — переход после отправки",
            passed=True, details="Навигации не произошло (возможно, AJAX-отправка без перезагрузки)",
        ))


# ============================================================================
# ГЛАВНАЯ ФУНКЦИЯ — связывает все проверки в единый прогон по всем сайтам
# ============================================================================

async def test_single_url(context: BrowserContext, url: str, reporter: Reporter, screenshots_dir: Path) -> None:
    """Прогоняет ПОЛНЫЙ набор проверок для одного сайта: smoke-тест, затем
    (если сайт вообще доступен) — скриншоты, битые ссылки, битые картинки
    и тест форм."""
    print(f"\n{'='*70}\nТестирую: {url}\n{'='*70}")

    page = await context.new_page()
    try:
        site_is_alive = await run_smoke_test(page, url, reporter)

        if not site_is_alive:
            # Нет смысла искать битые ссылки на странице, которая сама не открылась
            print(f"⏭  Сайт недоступен — остальные проверки для {url} пропущены.")
            return

        await check_broken_links(page, url, reporter)
        await check_broken_images(page, url, reporter)
        await test_forms(page, url, reporter)
    finally:
        await page.close()

    # Скриншоты снимаются отдельно, так как для каждого разрешения экрана
    # нужна отдельная страница с своим viewport
    await take_responsive_screenshots(context, url, reporter, screenshots_dir)


async def main() -> None:
    """Точка входа: запускает браузер, прогоняет все сайты из config.py по
    очереди, сохраняет итоговый отчёт."""
    reporter = Reporter(config.OUTPUT_DIR)
    screenshots_dir = Path(config.OUTPUT_DIR) / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    print(f"Запускаю тестирование {len(config.URLS_TO_TEST)} сайт(ов)...")
    print(f"Реальная отправка форм: {'ВКЛЮЧЕНА' if config.ACTUALLY_SUBMIT_FORMS else 'выключена (безопасный режим)'}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Один общий "контекст" (это как один профиль браузера) на весь прогон —
        # так все проверки для всех сайтов идут в одинаковых условиях.
        context = await browser.new_context()

        for url in config.URLS_TO_TEST:
            try:
                await test_single_url(context, url, reporter, screenshots_dir)
            except Exception as e:
                # Подстраховка: даже если для одного сайта что-то пошло совсем
                # не по плану, это не должно останавливать проверку остальных
                reporter.add(CheckResult(
                    category="Общая ошибка", url=url, name="Не удалось протестировать сайт",
                    passed=False, details=str(e),
                ))

        await context.close()
        await browser.close()

    text_log_path = reporter.save_text_log()
    html_report_path = reporter.save_html_report()

    print(f"\n{'='*70}")
    print(f"ГОТОВО. Всего проверок: {reporter.total} | Успешно: {reporter.passed_count} | Ошибок: {reporter.failed_count}")
    print(f"Текстовый лог:  {text_log_path.resolve()}")
    print(f"HTML-отчёт:     {html_report_path.resolve()}")
    print(f"Скриншоты:      {screenshots_dir.resolve()}")
    print(f"{'='*70}")


if __name__ == "__main__":
    asyncio.run(main())
