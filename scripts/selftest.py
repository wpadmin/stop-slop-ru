#!/usr/bin/env python3
"""selftest — самопроверка корпуса markers.yaml.

Четыре группы проверок:
1. Схема: уникальные id, обязательные поля, допустимые category и severity.
2. Компиляция: каждый regex собирается без ошибок.
3. Самотест regex↔bad: каждый regex ловит собственные bad-примеры.
   Исключения задокументированы ниже: справочник-only, покрытие соседним
   маркером, многострочные блоки.
4. Чистый текст: ни один regex не срабатывает на эталонных живых фразах —
   защита от ложных позитивов, включая регрессии из CHANGELOG.

Запуск:
    python3 scripts/selftest.py

Код выхода: 0 — всё чисто, 1 — есть провалы. Годится для CI.
"""

from __future__ import annotations

import re

from stopslop import (
    MARKERS_FILE,
    compile_marker,
    density_hit,
    load_markers,
    section_template_flag,
    uniform_shape_flag,
)

CATEGORIES = {"phrases", "structures", "punctuation", "formatting", "morphology"}
SEVERITIES = {"high", "medium", "low"}
REQUIRED = ("id", "category", "severity", "title", "why", "fix")

# bad-примеры, у которых нет и не должно быть regex-покрытия:
# «регуляркой их не отличить от живой речи». rule-of-three ловит только анафору
# «без X, без Y, без Z»; одиночная тройка «X, Y и Z» — eyeball-only (CHANGELOG 1.2.0).
REFERENCE_ONLY = {
    ("anglo-calque", "на самом деле, это"),
    ("artificial-wordforms", "нейросетевой (как украшение)"),
    # ветка пар «X-о-Yный» убрана из regex в 1.3.0: словарные композиты
    # («научно-технический», «юго-западный») от склеек не отличить
    ("artificial-wordforms", "маркетингово-аналитический"),
    ("rule-of-three", "быстро, качественно и надёжно (риторическая тройка — проверяй глазами)"),
    ("rule-of-three", "повышает скорость, качество и эффективность (проверяй глазами)"),
}

# bad-примеры, которые по замыслу ловит другой маркер (см. fix-примечание
# в самой записи). Проверяем, что чужой regex их действительно ловит.
CROSS_COVERED = {
    ("binary-contrast", "это не X — это Y"): "em-dash-overuse",
}

# маркеры, у которых bad-примеры — строки одного блока: regex обязан поймать
# блок целиком, а одиночную строку ловить не должен (см. fix-примечание).
BLOCK_MARKERS = {"bold-lead-bullets"}

# Эталон чистого текста: живые фразы, на которых корпус обязан молчать.
# Часть строк — регрессии, которые чинились в CHANGELOG.
CLEAN_TEXT = """\
Утром мы обновили серверы, и биллинг стал отвечать за двадцать миллисекунд.
Кэш — это временное хранилище: он экономит один запрос к базе.
Расположите блоки таким образом, чтобы сетка не ломалась на планшете.
В базе есть уникальный идентификатор записи и уникальный индекс по дате.
Перечитал классиков и современников, потом взялся за письма Пушкина.
Озябшая, дрожа всем телом, она вошла в дом и села к печке.
Он проснулся в мире, где никого не было.
Если завтра пойдёт дождь, перенесём съёмку на четверг.
Договор подписали ещё в марте, но платёж завис у банка.
На рынке взял картошку, лук и морковь.
Проект вырос из ночного прототипа: скучное автоматизировали, остальное оставили людям.
Научно-технический прогресс добрался и до юго-западных районов.
Он был принят в университет ещё летом.
Завод выпускает бесшовные трубы для газопроводов.
В самый важный момент он просто ушёл.
Удобный способ оплаты подключается в настройках банка.
Врач назначил антибиотик широкого спектра действия.
Он зашёл не просто так, а с новостями от сестры.
Мы долго спорили, но в итоге всё решили за час. Так бывает. Работа есть работа.
— Знакомо? — спросил он и сам же ответил.
У меня для тебя отличная новость: мы едем в отпуск!
Я люблю, когда ты меня обнимаешь.
Когда тебя предают, это больно.
Пер. Ф. Э. Роббинса, Loeb Classical Library, 1940.
К. Г. Юнг. Синхронистичность, 1952.
Похоже, дождь зарядил до вечера.
У каждой темы свой дом: брак — седьмой, карьера — десятый, дом — четвёртый.
Появляются важные связи, иногда — возможность войти в новый круг.
Он старался произвести впечатление, но переиграл.
Многовековая рыболовная и аграрная традиция выделяет водные знаки.
Пищевая промышленность и добывающая отрасль дали половину роста.
Связь практичная и заботливая, но требующая терпения к придиркам.
Красивая, выразительная шея и покатые плечи.
Дайте ему работу, в которой нужно понимать людей.
Метод простой, но работает только при точном времени рождения.
"""

# section-template: одни и те же разделы с колодкой и без неё
_FACT = (
    "В 1840 году обсерватория в Пулкове получила рефрактор с объективом "
    "в пятнадцать дюймов, и наблюдения двойных звёзд пошли вдвое быстрее."
)
TEMPLATED_SECTIONS = "".join(
    f"## Раздел {i}\n\n{_FACT} {_FACT}\n\n"
    "Сегодня этот инструмент считают устаревшим, но каталог двойных звёзд "
    "из Пулкова цитируют до сих пор.\n\n"
    "Подробнее о каталоге — на странице обсерватории.\n\n"
    for i in range(4)
)
LIVE_SECTIONS = "".join(
    f"## Раздел {i}\n\n{_FACT} {_FACT} {_FACT}\n\n"
    "Заказ на объектив ушёл в Мюнхен, в мастерскую Мерца и Малера, "
    "и стекло шлифовали почти два года.\n\n"
    for i in range(4)
)
# uniform-shape: пакет страниц одной мерки и тот же пакет с объёмом от материала
_PAGE_PAR = " ".join([_FACT] * 3)
UNIFORM_PAGES = "".join(
    f"## Страница {i}\n\n" + "\n\n".join([_PAGE_PAR] * 3) + "\n\n" for i in range(5)
)
VARIED_PAGES = "".join(
    f"## Страница {i}\n\n" + "\n\n".join([_PAGE_PAR] * n) + "\n\n"
    for i, n in enumerate((2, 5, 3, 7, 4))
)
HEDGE_SERIES = (
    "Похоже, так и было. Насколько нам известно, это не проверяли. "
    "Кто автор, мы так и не нашли."
)

# пояснение в скобках в конце bad-примера — комментарий, не часть фразы
ANNOTATION = re.compile(r"\s*\([^()]*\)$")


def check_schema(markers: list[dict]) -> list[str]:
    fails: list[str] = []
    seen: set[str] = set()
    for m in markers:
        mid = m.get("id", "<без id>")
        if mid in seen:
            fails.append(f"схема: дубль id {mid}")
        seen.add(mid)
        for field in REQUIRED:
            if not m.get(field):
                fails.append(f"схема: {mid}: нет поля {field}")
        if m.get("category") not in CATEGORIES:
            fails.append(f"схема: {mid}: category {m.get('category')!r} вне списка")
        if m.get("severity") not in SEVERITIES:
            fails.append(f"схема: {mid}: severity {m.get('severity')!r} вне списка")
        if not m.get("bad"):
            fails.append(f"схема: {mid}: пустой список bad")
    # устаревшее исключение — тоже провал: пара должна существовать в корпусе
    pairs = {(m.get("id"), b) for m in markers for b in m.get("bad", [])}
    for mid, bad in REFERENCE_ONLY | set(CROSS_COVERED):
        if (mid, bad) not in pairs:
            fails.append(f"схема: исключение ({mid}, «{bad}») не найдено в корпусе")
    return fails


def check_coverage(markers: list[dict], compiled: dict) -> list[str]:
    fails: list[str] = []
    for m in markers:
        mid = m["id"]
        rx = compiled.get(mid)
        if rx is None:
            continue
        if mid in BLOCK_MARKERS:
            if not rx.search("\n".join(m.get("bad", []))):
                fails.append(f"покрытие: {mid}: regex не ловит блок bad-примеров")
            for bad in m.get("bad", []):
                if rx.search(bad):
                    fails.append(
                        f"покрытие: {mid}: одиночный пункт не должен ловиться: «{bad}»"
                    )
            continue
        for bad in m.get("bad", []):
            if (mid, bad) in REFERENCE_ONLY:
                continue
            probe = ANNOTATION.sub("", bad)
            other = CROSS_COVERED.get((mid, bad))
            if other is not None:
                orx = compiled.get(other)
                if orx is None or not orx.search(probe):
                    fails.append(f"покрытие: {mid}: «{bad}» должен ловить {other}")
                continue
            if not rx.search(probe):
                fails.append(f"покрытие: {mid}: regex не ловит свой пример «{bad}»")
    return fails


def check_clean(compiled: dict, by_id: dict) -> list[str]:
    fails: list[str] = []
    for mid, rx in sorted(compiled.items()):
        if by_id[mid].get("density"):
            if density_hit(by_id[mid], rx, CLEAN_TEXT):
                fails.append(f"чистый текст: {mid} ложно сработал как серия")
            continue
        mo = rx.search(CLEAN_TEXT)
        if mo:
            frag = mo.group(0).strip().replace("\n", " ")
            fails.append(f"чистый текст: {mid} ложно сработал: «{frag[:60]}»")
    return fails


def check_heuristics(compiled: dict, by_id: dict) -> list[str]:
    fails: list[str] = []
    lexicons = [compiled[i] for i in ("epistemic-hedging", "clever-hinge") if i in compiled]
    if not section_template_flag(TEMPLATED_SECTIONS, lexicons):
        fails.append("эвристика: section-template не видит колодку разделов")
    if section_template_flag(LIVE_SECTIONS, lexicons):
        fails.append("эвристика: section-template сработал на разделах без колодки")
    if not uniform_shape_flag(UNIFORM_PAGES):
        fails.append("эвристика: uniform-shape не видит страницы одной мерки")
    if uniform_shape_flag(VARIED_PAGES):
        fails.append("эвристика: uniform-shape сработал на страницах разного объёма")
    hedging = by_id.get("epistemic-hedging")
    if hedging and not density_hit(hedging, compiled["epistemic-hedging"], HEDGE_SERIES):
        fails.append("эвристика: epistemic-hedging не видит серию из трёх оговорок")
    return fails


def main() -> int:
    markers = load_markers(MARKERS_FILE)
    if not markers:
        print("FAIL: markers.yaml не распарсился — ни одного маркера")
        return 1

    fails = check_schema(markers)

    compiled: dict = {}
    for m in markers:
        if not m.get("regex"):
            continue
        rx = compile_marker(m)  # при ошибке сам пишет warn в stderr
        if rx is None:
            fails.append(f"компиляция: {m['id']}: regex не собрался")
        else:
            compiled[m["id"]] = rx

    by_id = {m["id"]: m for m in markers}
    fails += check_coverage(markers, compiled)
    fails += check_clean(compiled, by_id)
    fails += check_heuristics(compiled, by_id)

    print(
        f"Маркеров: {len(markers)}, с regex: {len(compiled)}, "
        f"справочник-only: {len(markers) - len(compiled)}."
    )
    if fails:
        for f in fails:
            print(f"FAIL: {f}")
        print(f"Провалов: {len(fails)}.")
        return 1
    print("OK: схема цела, регулярки ловят свои примеры и молчат на чистом тексте.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
