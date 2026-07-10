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

from stopslop import MARKERS_FILE, compile_marker, load_markers

CATEGORIES = {"phrases", "structures", "punctuation", "formatting", "morphology"}
SEVERITIES = {"high", "medium", "low"}
REQUIRED = ("id", "category", "severity", "title", "why", "fix")

# bad-примеры, у которых нет и не должно быть regex-покрытия:
# «регуляркой их не отличить от живой речи». rule-of-three ловит только анафору
# «без X, без Y, без Z»; одиночная тройка «X, Y и Z» — eyeball-only (CHANGELOG 1.2.0).
REFERENCE_ONLY = {
    ("anglo-calque", "на самом деле, это"),
    ("artificial-wordforms", "нейросетевой (как украшение)"),
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
"""

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


def check_clean(compiled: dict) -> list[str]:
    fails: list[str] = []
    for mid, rx in sorted(compiled.items()):
        mo = rx.search(CLEAN_TEXT)
        if mo:
            frag = mo.group(0).strip().replace("\n", " ")
            fails.append(f"чистый текст: {mid} ложно сработал: «{frag[:60]}»")
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

    fails += check_coverage(markers, compiled)
    fails += check_clean(compiled)

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
