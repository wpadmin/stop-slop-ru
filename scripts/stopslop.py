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

# маркеры без regex, которые считает код этого файла
HEURISTICS = {"flat-rhythm", "section-template", "uniform-shape"}


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

    escapes = {'"': '"', "\\": "\\", "n": "\n", "t": "\t"}

    def unquote(v: str) -> str:
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] == '"':
            # двойные кавычки YAML: escape-последовательности декодируются
            # одним проходом слева направо; цепочка .replace() разобрала бы
            # "\\n" как бэкслеш с переводом строки
            body = v[1:-1]
            out: list[str] = []
            i = 0
            while i < len(body):
                ch = body[i]
                if ch == "\\" and i + 1 < len(body):
                    nxt = body[i + 1]
                    out.append(escapes.get(nxt, "\\" + nxt))
                    i += 2
                else:
                    out.append(ch)
                    i += 1
            return "".join(out)
        if len(v) >= 2 and v[0] == v[-1] and v[0] == "'":
            # одинарные кавычки YAML: только '' -> ', без escape
            return v[1:-1].replace("''", "'")
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


RE_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}


def compile_marker(m: dict):
    pat = m.get("regex", "") or ""
    if not pat:
        return None
    flags = 0
    for ch in m.get("flags") or "im":
        if ch in RE_FLAGS:
            flags |= RE_FLAGS[ch]
        elif ch.strip():
            sys.stderr.write(f"warn: неизвестный флаг {ch!r} у маркера {m['id']}\n")
    try:
        return re.compile(pat, flags)
    except re.error as exc:
        sys.stderr.write(f"warn: regex маркера {m['id']} не скомпилировался: {exc}\n")
        return None


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


WORD = re.compile(r"[\wёЁ]+(?:-[\wёЁ]+)*")


def word_count(text: str) -> int:
    return len(WORD.findall(text))


# меньше трёх совпадений — не серия
DENSITY_MIN_HITS = 3


def density_hit(m: dict, rx, text: str) -> dict | None:
    found = list(rx.finditer(text))
    per = len(found) * 1000 / (word_count(text) or 1)
    if len(found) < DENSITY_MIN_HITS or per < float(m["density"]):
        return None
    lines = sorted({line_of(text, mo.start()) for mo in found})
    samples = "; ".join(f"«{mo.group(0).strip()}»" for mo in found[:5])
    tail = "; …" if len(found) > 5 else ""
    return {
        "line": lines[0],
        "detail": (
            f"совпадений: {len(found)}, {per:.1f} на 1000 слов (порог {m['density']}), "
            f"строки {', '.join(map(str, lines))}\n{samples}{tail}"
        ),
    }


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
            "line": 0,
            "detail": f"разброс длин предложений мал (CV={cv:.2f}); чередуй длинное и короткое",
        }
    return None


# section-template; калибровка порогов — в CHANGELOG 1.5.0
SECTION_TEMPLATE_SHARE = 0.5
SECTION_TEMPLATE_MIN = 4
SECTION_MIN_WORDS = 60

HEADING = re.compile(r"^#{2,3} +(.+)$", re.M)
SECTION_SKIP = re.compile(
    r"^(примечани|источник|литератур|ссылки|см\. также|частые вопросы|вопросы и ответы|faq)",
    re.I,
)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+(?=[«\"(]?[А-ЯЁA-Z0-9])")
ORDINAL_START = re.compile(
    r"^(сначала|потом|затем|дальше|далее|во-первых|во-вторых|в-третьих|наконец"
    r"|перв\w*|втор\w*|трет\w*|четв\w*|последн\w*)\b",
    re.I,
)
MODERN_TURN = re.compile(
    r"\b(сегодня|современн\w*|нынешн\w*|до сих пор|по-прежнему|в наши дни|давно не|теперь)\b",
    re.I,
)
# концовка раздела — мостик на сервис или соседнюю статью
CROSSLINK = re.compile(
    r"калькулятор|сервис|на странице|в разделе|по ссылке|подробнее|читайте|смотрите"
    r"|попробуйте|закажите|введите|можно посмотреть|описан\w* на|стать[яеюи] (про|о)\b"
    r"|разбор|расч[её]т|\bмы\s+[а-яё]+(?:ем|ём|им)\b",
    re.I,
)


def split_sentences(par: str) -> list[str]:
    return [s.strip() for s in SENTENCE_SPLIT.split(par) if s.strip()]


def body_sections(text: str) -> list[tuple[int, str, list[str]]]:
    heads = list(HEADING.finditer(text))
    out = []
    for i, h in enumerate(heads):
        title = h.group(1).strip()
        if SECTION_SKIP.match(title) or title.endswith("?"):
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        paras = [
            p.strip()
            for p in text[h.end() : end].split("\n\n")
            if p.strip() and not p.lstrip().startswith(("-", "*", "|", ">", "«"))
        ]
        if sum(word_count(p) for p in paras) >= SECTION_MIN_WORDS:
            out.append((line_of(text, h.start()), title, paras))
    return out


def section_template_flag(text: str, lexicons: list) -> dict | None:
    """Раздел по колодке: внутри поворот, последнее предложение — ход-вывод."""
    sections = body_sections(text)
    if len(sections) < SECTION_TEMPLATE_MIN:
        return None
    matched = []
    for line, title, paras in sections:
        turn = False
        for p in paras:
            ss = split_sentences(p)
            if (
                ss
                and word_count(ss[0]) <= 6
                and word_count(p) >= 20
                and not ORDINAL_START.match(ss[0])
            ):
                turn = True  # вердикт в зачине абзаца
            if MODERN_TURN.search(p) or any(rx.search(p) for rx in lexicons):
                turn = True
        last = split_sentences(paras[-1])[-1]
        closing = (
            CROSSLINK.search(last)
            or any(rx.search(last) for rx in lexicons)
            or word_count(last) <= 7
        )
        if turn and closing:
            matched.append((line, title, last))
    share = len(matched) / len(sections)
    if not matched or share < SECTION_TEMPLATE_SHARE:
        return None
    rows = "\n".join(f"строка {ln}, «{t[:40]}»: …{last[-70:]}" for ln, t, last in matched)
    return {
        "line": matched[0][0],
        "detail": (
            f"{len(matched)} из {len(sections)} разделов по одной колодке "
            f"(доля {share:.2f}, порог {SECTION_TEMPLATE_SHARE}): внутри поворот, "
            f"под занавес ход-вывод\n{rows}"
        ),
    }


# uniform-shape; калибровка порогов — в CHANGELOG
UNIFORM_MIN_SECTIONS = 4
UNIFORM_MIN_PARAS = 3
UNIFORM_MIN_MEAN_WORDS = 150  # короткие параллельные карточки ровны по жанру
UNIFORM_LENGTH_CV = 0.10
UNIFORM_PARAS_SHARE = 0.75


def uniform_shape_flag(text: str) -> dict | None:
    """Разделы одного размера и с одним числом абзацев: серия по одной мерке."""
    sections = body_sections(text)
    if len(sections) < UNIFORM_MIN_SECTIONS:
        return None
    sizes = [sum(word_count(p) for p in paras) for _, _, paras in sections]
    counts = [len(paras) for _, _, paras in sections]
    mean = sum(sizes) / len(sizes)
    cv = (sum((x - mean) ** 2 for x in sizes) / len(sizes)) ** 0.5 / mean
    top = max(set(counts), key=counts.count)
    share = counts.count(top) / len(counts)
    if (
        mean < UNIFORM_MIN_MEAN_WORDS
        or top < UNIFORM_MIN_PARAS
        or cv >= UNIFORM_LENGTH_CV
        or share < UNIFORM_PARAS_SHARE
    ):
        return None
    return {
        "line": sections[0][0],
        "detail": (
            f"{len(sections)} разделов одного размера: {min(sizes)}–{max(sizes)} слов "
            f"(CV={cv:.2f}, порог {UNIFORM_LENGTH_CV}), по {top} абзацев в "
            f"{counts.count(top)} из {len(counts)}"
        ),
    }


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
        default=None,
        help="вернуть код 1, если найдено больше N срабатываний (0 — при любом)",
    )
    args = ap.parse_args()

    try:
        markers = load_markers(MARKERS_FILE)
    except OSError as exc:
        sys.stderr.write(f"ошибка: не смог прочитать {MARKERS_FILE}: {exc}\n")
        return 2

    if args.list:
        for m in markers:
            if m.get("density"):
                tail = f"  [серия: от {m['density']} на 1000 слов]"
            elif m["id"] in HEURISTICS:
                tail = "  [эвристика]"
            elif not m.get("regex"):
                tail = "  [без regex]"
            else:
                tail = ""
            print(
                f"{m['severity']:<6} {m['category']:<11} {m['id']:<22} "
                f"{m.get('title', '')}{tail}"
            )
        return 0

    if not args.file:
        ap.error(
            "нужен путь к файлу или '-' для stdin (или --list для списка маркеров)"
        )

    sev_filter = (
        {s.strip() for s in args.severity.split(",")} if args.severity else None
    )
    cat_filter = (
        {c.strip() for c in args.category.split(",")} if args.category else None
    )
    id_filter = {i.strip() for i in args.ids.split(",")} if args.ids else None

    def keep(m: dict) -> bool:
        if sev_filter and m.get("severity") not in sev_filter:
            return False
        if cat_filter and m.get("category") not in cat_filter:
            return False
        if id_filter and m.get("id") not in id_filter:
            return False
        return True

    if args.file == "-":
        text = sys.stdin.read()
    else:
        try:
            text = Path(args.file).read_text(encoding="utf-8")
        except FileNotFoundError:
            sys.stderr.write(f"ошибка: файл не найден — {args.file}\n")
            return 2
        except (OSError, UnicodeDecodeError) as exc:
            sys.stderr.write(f"ошибка: не смог прочитать {args.file}: {exc}\n")
            return 2

    compiled = {m["id"]: rx for m in markers if (rx := compile_marker(m))}

    # (severity, category, id, title, строка, фрагмент или сводка, fix, сводка?)
    hits: list[tuple] = []

    def add_summary(m: dict, found: dict | None) -> None:
        if found:
            hits.append(
                (
                    m["severity"],
                    m["category"],
                    m["id"],
                    m.get("title", ""),
                    found["line"],
                    found["detail"],
                    m.get("fix", ""),
                    True,
                )
            )

    for m in markers:
        if not keep(m):
            continue
        rx = compiled.get(m["id"])
        if not rx:
            continue
        if m.get("density"):
            add_summary(m, density_hit(m, rx, text))
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
                    frag[:90],
                    m.get("fix", ""),
                    False,
                )
            )

    # эвристики: тяжесть и категорию берут из своей записи в markers.yaml
    by_id = {m["id"]: m for m in markers}
    if "flat-rhythm" in by_id and keep(by_id["flat-rhythm"]):
        add_summary(by_id["flat-rhythm"], rhythm_flag(text))
    if "section-template" in by_id and keep(by_id["section-template"]):
        lexicons = [
            compiled[i] for i in ("epistemic-hedging", "clever-hinge") if i in compiled
        ]
        add_summary(by_id["section-template"], section_template_flag(text, lexicons))

    if "uniform-shape" in by_id and keep(by_id["uniform-shape"]):
        add_summary(by_id["uniform-shape"], uniform_shape_flag(text))

    hits.sort(key=lambda h: (-SEVERITY_ORDER.get(h[0], 0), h[4]))

    if not hits:
        print("Чисто: маркеров не найдено.")
        return 0

    by_sev: dict[str, int] = {}
    for sev, cat, mid, title, ln, frag, fix, summary in hits:
        by_sev[sev] = by_sev.get(sev, 0) + 1
        loc = f"строка {ln}" if ln else "—"
        print(f"[{sev.upper()}] {title} ({mid}), {loc}")
        if summary:
            first, *rest = frag.split("\n")
            print(f"    что:   {first}")
            for extra in rest:
                print(f"           {extra}")
        else:
            print(f"    нашёл: «{frag}»")
        if fix:
            print(f"    как:   {fix}")
        print()

    summary_line = ", ".join(
        f"{k}: {by_sev[k]}" for k in ("high", "medium", "low") if k in by_sev
    )
    print(f"Итого срабатываний: {len(hits)} ({summary_line}).")

    if args.min_score is not None and len(hits) > args.min_score:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
