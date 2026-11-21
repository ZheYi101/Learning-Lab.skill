"""Read the personal knowledge inventory without updating any learning record."""

import argparse
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote


SETTINGS = Path(__file__).resolve().parents[1] / "knowledge-base.local.json"
STATES = {"待确认", "需回顾", "提示可用", "独立可用"}
EVIDENCE = {"自述", "作答", "实作", "材料"}
EXPERIENCES = {"课程", "语言", "项目", "自学", "其他"}


def prose(lines):
    """Leave Markdown examples and fenced source code outside managed fields."""
    fence = ""
    for i, line in enumerate(lines):
        candidate = line.lstrip()
        if fence:
            if candidate.rstrip().startswith(fence) and not candidate.strip(fence[0]).strip():
                fence = ""
            continue
        match = re.match(r"(`{3,}|~{3,})", candidate)
        if match:
            fence = match[1]
        else:
            yield i, line


def experience_targets(text):
    inline = r"\[((?:\\.|[^\]\\\n])+)\]\(\s*(?:<([^>]+)>|((?:\\.|[^()\s]|\([^()]*\))+))\s*\)"
    for link in re.finditer(inline, text):
        label = re.sub(r"\\(.)", r"\1", link[1])
        target = unquote((link[2] or link[3]).split("#", 1)[0])
        yield label, re.sub(r"\\([() ])", r"\1", target), False
    for link in re.finditer(r"\[\[([^]|#]+)(?:#[^]|]*)?(?:\|([^]]+))?\]\]", text):
        yield link[2] or "", unquote(link[1].strip()).replace("\\", "/"), True


@dataclass
class Note:
    path: Path
    title: str
    tags: str
    body: str

    def field(self, name, allowed):
        prefix = f"#{name}/"
        values = {tag[len(prefix):] for tag in self.tags.split() if tag.startswith(prefix)}
        if len(values) == 1 and values <= allowed:
            return values.pop()
        return "待校对"

    def status(self):
        state = self.field("状态", STATES)
        basis = self.field("依据", EVIDENCE)
        if basis == "待校对" or (basis == "材料" and state != "待确认"):
            state = "待校对"
        return state, basis

    def line(self, *labels):
        for _, line in prose(self.body.splitlines()):
            label, separator, value = line.partition("｜")
            if separator and label.removeprefix("- ") in labels:
                return value.strip()
        return ""

    def recent(self):
        return self.line("最近记录")

    def experience_names(self):
        names = []
        for _, line in prose(self.body.splitlines()):
            if not line.startswith("- 相关经历｜"):
                continue
            for label, target, wiki in experience_targets(line):
                path = Path(target)
                if not wiki and ("经历" not in path.parts or path.suffix.casefold() != ".md"):
                    continue
                names.append(path.stem)
                if label:
                    names.append(label)
        return list(dict.fromkeys(names))

    def cues(self):
        return [tag[4:] for tag in self.tags.split() if tag.startswith("#线索/") and tag[4:]]


def notes(root, group):
    folder = root / group
    if not folder.is_dir():
        raise FileNotFoundError(f"找不到知识库目录 {folder}")
    for path in sorted(folder.rglob("*.md")):
        body = path.read_text(encoding="utf-8-sig")
        lines = (line.strip() for line in body.splitlines() if line.strip())
        heading = next(lines, "")
        title = heading.removeprefix("# ") if heading.startswith("# ") else path.stem
        yield Note(path, title, next(lines, ""), body)


def inventory(root):
    kinds = Counter(note.field("类型", EXPERIENCES) for note in notes(root, "经历"))
    states = {}
    for note in notes(root, "能力"):
        state, basis = note.status()
        states.setdefault(state, Counter())[basis] += 1
    return {
        "经历数": sum(kinds.values()),
        "按经历类型": kinds,
        "能力数": sum(sum(counts.values()) for counts in states.values()),
        "按当前状态和依据": states,
    }


def normalized(text):
    return unicodedata.normalize("NFKC", text).casefold()


def relation_cues(root, limit, keywords=()):
    counts = Counter()
    for group in ("能力", "经历"):
        for note in notes(root, group):
            for cue in sorted(set(note.cues())):
                if not keywords or any(mentioned(term, normalized(cue)) for term in keywords):
                    counts[cue] += 1
    return dict(counts.most_common(limit))


def preferences(root):
    path = root / "学习偏好.md"
    if not path.exists():
        return []
    active = []
    for _, line in prose(path.read_text(encoding="utf-8-sig").splitlines()):
        if not line.startswith("- 偏好｜"):
            continue
        parts = line.split("｜", 4)
        if len(parts) != 5:
            raise ValueError(f"偏好行格式不完整：{path}")
        _, key, status, source, text = parts
        if status not in {"明确", "试用", "停用"}:
            raise ValueError(f"偏好状态无效：{key}")
        if status != "停用":
            active.append({"偏好": key, "状态": status, "依据": source, "用法": text})
    return active


def mentioned(phrase, text):
    # Keep Latin abbreviations such as CI from matching inside artificial.
    left = r"(?<![a-z0-9])" if phrase[:1].isascii() and phrase[:1].isalnum() else ""
    right = r"(?![a-z0-9])" if phrase[-1:].isascii() and phrase[-1:].isalnum() else ""
    return re.search(left + re.escape(phrase) + right, text) is not None


def recall(root, terms, limit):
    hits = []
    for group in ("能力", "经历"):
        for note in notes(root, group):
            recent = note.recent()
            guidance = note.line("教学借用") or note.line("条件与适用范围", "适用范围", "下次学习", "范围")
            cues = [normalized(cue) for cue in note.cues()]
            # A question can contain a known cue. Additional arguments can name the relation explicitly.
            experiences = [normalized(name) for name in note.experience_names()]
            names = [normalized(note.title), *cues, *experiences]
            embedded = [name for name in names if any(mentioned(name, term) for term in terms)]
            candidates = dict.fromkeys([*terms, *embedded])
            fields = [normalized(note.title), normalized(recent), normalized(guidance)]
            title, observed, transfer = (
                [t for t in candidates if mentioned(t, value)] for value in fields
            )
            named = [t for t in candidates if t in cues]
            related = [t for t in candidates if t in experiences]
            matches = list(dict.fromkeys(named + title + observed + transfer + related))
            if not matches or not recent:
                continue
            result = {"条目": note.title, "命中词": matches, "已有记录": recent}
            if related:
                result["命中关联经历"] = related
            suggestions = [term for term in matches if term not in title and term not in observed and term not in related]
            if suggestions:
                result["仅见于线索或教学建议"] = suggestions
            if guidance:
                result["教学参考"] = guidance
            if group == "能力":
                result["状态"], result["依据"] = note.status()
            else:
                result["类型"] = note.field("类型", EXPERIENCES)
            result["文件"] = str(note.path)
            direct = set(named + title)
            # Within one course or project, prefer the most recently observed related ability.
            date = re.match(r"\d{4}-\d{2}-\d{2}", recent) if related else None
            observed_on = int(date[0].replace("-", "")) if date else 0
            rank = (-len(direct), -len(matches), not related, -observed_on, group != "能力", -len(observed), str(note.path))
            hits.append((rank, result))
    hits.sort(key=lambda hit: hit[0])
    return [result for _, result in hits[:limit]]


def search(root, group, terms, limit):
    hits = []
    groups = ("能力", "经历") if group == "全部" else (group,)
    for section in groups:
        for note in notes(root, section):
            body = normalized(f"{note.path.stem}\n{note.body}")
            matches = [term for term in terms if mentioned(term, body)]
            if not matches:
                continue
            recent = note.recent()
            current = normalized(f"{note.path.stem} {note.title} {note.tags} {recent}")
            current_hits = sum(mentioned(term, current) for term in matches)
            result = {"条目": note.title, "命中词": matches, "文件": str(note.path)}
            if recent:
                result["最近记录"] = recent[:160]
            if section == "能力":
                result["状态"], result["依据"] = note.status()
            else:
                result["类型"] = note.field("类型", EXPERIENCES)
            rank = (-current_hits, -len(matches), section != "能力", str(note.path))
            hits.append((rank, result))
    hits.sort(key=lambda hit: hit[0])
    return [result for _, result in hits[:limit]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="使用指定的本地知识库目录")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("stats", help="按经历类型、能力状态和依据统计")
    commands.add_parser("preferences", help="读取当前明确或试用的个人学习偏好")
    cues = commands.add_parser("cues", help="列出库中实际用过的关系词，供跨领域类比查找")
    cues.add_argument("keywords", nargs="*", help="在全部关系词中筛选，例如 任务 或 并发")
    cues.add_argument("--limit", type=int, default=24)
    teach = commands.add_parser("recall", help="按学习问题或关系词取回旧例和适用范围")
    teach.add_argument("terms", nargs="+")
    teach.add_argument("--limit", type=int, default=3)
    teach.add_argument("--with-preferences", action="store_true", help="同时取回当前教学偏好")
    find = commands.add_parser("find", help="查找含任一主题词或关系词的记录")
    find.add_argument("terms", nargs="+")
    find.add_argument("--group", choices=("能力", "经历", "全部"), default="能力")
    find.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    if args.command in {"cues", "recall", "find"} and args.limit < 1:
        parser.error("返回条数需要大于零")
    try:
        root = args.root or Path(json.loads(SETTINGS.read_text(encoding="utf-8-sig"))["root"])
        root = root.expanduser()
        if not root.is_absolute():
            parser.error("知识库路径需要使用绝对路径")
        if args.command == "stats":
            result = inventory(root)
        elif args.command == "preferences":
            result = preferences(root)
        elif args.command == "cues":
            result = relation_cues(root, args.limit, [normalized(key) for key in args.keywords if key.strip()])
        else:
            terms = list(dict.fromkeys(normalized(word.strip()) for word in args.terms if word.strip()))
            if not terms:
                parser.error("请给出至少一个检索词")
            if args.command == "recall":
                result = recall(root, terms, args.limit)
                if args.with_preferences:
                    result = {"学习偏好": preferences(root), "旧例": result}
            else:
                result = search(root, args.group, terms, args.limit)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"读取个人知识库失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
