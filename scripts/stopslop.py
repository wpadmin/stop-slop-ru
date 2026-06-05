#!/usr/bin/env python3
"""stopslop — точечный линтер AI-маркеров русской прозы.

Источник правил — markers.yaml рядом со скиллом. Один файл правил, два
потребителя: человек (references/*.md) и этот линтер. Добавил маркер с полем
`regex` — он сразу ловится здесь. См. CONTRIBUTING.md.

Запуск:
    python3 scripts/stopslop.py text.md              # весь корпус
    python3 scripts/stopslop.py text.md --severity high
    python3 scripts/stopslop.py text.md --category punctuation,phrases
    python3 scripts/stopslop.py text.md --id em-dash-overuse,binary-contrast
    cat text.md | python3 scripts/stopslop.py -      # из stdin
    python3 scripts/stopslop.py text.md --list       # показать все маркеры

Зависимостей нет: YAML парсится встроенным мини-парсером под формат markers.yaml.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MARKERS_FILE = Path(__file__).resolve().parent.parent / "markers.yaml"

SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1}


def load_markers(path: Path) -> list[dict]:
    """Минимальный парсер markers.yaml.

    Поддерживает только формы, которые встречаются в этом файле: верхнеуровневый
    список `markers:` из записей со скалярными полями и списком `bad:`. Не общий
    YAML — намеренно, чтобы не тащить зависимость. Если структура файла усложнится,
    замените на `import yaml`.
    """
    text = path.read_text(encoding="utf-8")
    markers: list[dict] = []
    cur: dict | None = None
    in_bad = False

    def unquote(v: str) -> str:
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] == '"':
            # двойные кавычки YAML: декодируем escape-последовательности
            v = v[1:-1]
            v = v.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        elif len(v) >= 2 and v[0] == v[-1] and v[0] == "'":
            # одинарные кавычки YAML: только '' -> ', без escape
            v = v[1:-1].replace("''", "'")
        return v

    for raw in text.splitlines():
        line = raw.rstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # новая запись маркера
        if re.match(r"^\s*-\s+id:\s*", line):
            if cur:
                markers.append(cur)
            cur = {"bad": []}
            in_bad = False
            cur["id"] = unquote(line.split("id:", 1)[1])
            continue
        if cur is None:
            continue
        # элемент списка bad:
        if in_bad and re.match(r"^\s*-\s+", line):
            cur["bad"].append(unquote(re.sub(r"^\s*-\s+", "", line)))
            continue
        m = re.match(r"^\s{2,}(\w+):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2)
            in_bad = key == "bad"
            if in_bad:
                continue
            cur[key] = unquote(val)
    if cur:
        markers.append(cur)
    return markers


def compile_marker(m: dict):
    pat = m.get("regex", "") or ""
    if not pat:
        return None
    flags = re.IGNORECASE | re.MULTILINE
    try:
        return re.compile(pat, flags)
    except re.error as exc:
        sys.stderr.write(f"warn: regex маркера {m['id']} не скомпилировался: {exc}\n")
        return None


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def rhythm_flag(text: str) -> dict | None:
    """Эвристика плоского ритма: маленький разброс длин предложений."""
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    lens = [len(s.split()) for s in sentences if len(s.split()) >= 3]
    if len(lens) < 6:
        return None
    mean = sum(lens) / len(lens)
    var = sum((x - mean) ** 2 for x in lens) / len(lens)
    std = var**0.5
    cv = std / mean if mean else 0
    if cv < 0.35:  # длины слишком ровные
        return {
            "id": "flat-rhythm",
            "title": "Плоский ритм (эвристика)",
            "severity": "low",
            "detail": f"разброс длин предложений мал (CV={cv:.2f}); чередуй длинное и короткое",
        }
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Линтер AI-маркеров русской прозы (stop-slop-ru)."
    )
    ap.add_argument("file", nargs="?", help="путь к файлу или '-' для stdin")
    ap.add_argument("--severity", help="фильтр: high,medium,low")
    ap.add_argument(
        "--category",
        help="фильтр: phrases,structures,punctuation,formatting,morphology",
    )
    ap.add_argument("--id", dest="ids", help="фильтр по id маркеров через запятую")
    ap.add_argument("--list", action="store_true", help="вывести все маркеры и выйти")
    ap.add_argument(
        "--min-score",
        type=int,
        default=0,
        help="вернуть код 1, если найдено больше N срабатываний",
    )
    args = ap.parse_args()

    markers = load_markers(MARKERS_FILE)

    if args.list:
        for m in markers:
            print(
                f"{m['severity']:<6} {m['category']:<11} {m['id']:<22} {m.get('title', '')}"
            )
        return 0

    if not args.file:
        ap.error(
            "нужен путь к файлу или '-' для stdin (или --list для списка маркеров)"
        )

    sev_filter = (
        set(s.strip() for s in args.severity.split(",")) if args.severity else None
    )
    cat_filter = (
        set(c.strip() for c in args.category.split(",")) if args.category else None
    )
    id_filter = set(i.strip() for i in args.ids.split(",")) if args.ids else None

    def keep(m: dict) -> bool:
        if sev_filter and m.get("severity") not in sev_filter:
            return False
        if cat_filter and m.get("category") not in cat_filter:
            return False
        if id_filter and m.get("id") not in id_filter:
            return False
        return True

    active = [m for m in markers if keep(m)]

    if args.file == "-":
        text = sys.stdin.read()
    else:
        path = Path(args.file)
        if not path.is_file():
            sys.stderr.write(f"ошибка: файл не найден — {args.file}\n")
            return 2
        text = path.read_text(encoding="utf-8")

    hits: list[tuple] = []
    for m in active:
        rx = compile_marker(m)
        if not rx:
            continue
        for mo in rx.finditer(text):
            frag = mo.group(0).strip().replace("\n", " ")
            hits.append(
                (
                    m["severity"],
                    m["category"],
                    m["id"],
                    m.get("title", ""),
                    line_of(text, mo.start()),
                    frag,
                    m.get("fix", ""),
                )
            )

    # эвристика ритма (если не отфильтрована)
    if keep({"severity": "low", "category": "structures", "id": "flat-rhythm"}):
        rf = rhythm_flag(text)
        if rf:
            hits.append(
                (
                    rf["severity"],
                    "structures",
                    rf["id"],
                    rf["title"],
                    0,
                    rf["detail"],
                    "чередуй длины предложений",
                )
            )

    hits.sort(key=lambda h: (-SEVERITY_ORDER.get(h[0], 0), h[4]))

    if not hits:
        print("Чисто: маркеров не найдено.")
        return 0

    by_sev: dict[str, int] = {}
    for sev, cat, mid, title, ln, frag, fix in hits:
        by_sev[sev] = by_sev.get(sev, 0) + 1
        loc = f"строка {ln}" if ln else "—"
        print(f"[{sev.upper()}] {title} ({mid}), {loc}")
        print(f"    нашёл: «{frag[:90]}»")
        if fix:
            print(f"    как:   {fix}")
        print()

    summary = ", ".join(
        f"{k}: {by_sev[k]}" for k in ("high", "medium", "low") if k in by_sev
    )
    print(f"Итого срабатываний: {len(hits)} ({summary}).")

    if args.min_score and len(hits) > args.min_score:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
