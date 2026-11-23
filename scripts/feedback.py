"""Store and compare lesson or paragraph feedback in a local Markdown table."""

import argparse
import codecs
from datetime import date
import html
import json
from pathlib import Path
import re
import sys
from typing import NamedTuple

from record import SETTINGS, checked, save, writer_lock


OVERALL_PREFIX = "整体 "
OLD_INTRO = "三项依次为好懂／读得顺／有趣，均为 1–5 分，越高体验越好。「—」表示还未评分。AI 栏是讲解发出前对阅读体验的估计。自己回填时可用 `4/2/3`，也可只在反馈栏写卡在哪里。"
INTRO = "整体读感和各分项均为 1–5 分，越高越好。「整体 4」表示整课评分，分项依次为好懂／读得顺／有趣。「—」表示未评分。AI 栏是发出前的预估，可以只在反馈栏写感受。"

HEADER = f"""# 教学段落反馈

{INTRO}

| 段号 | 日期 | 主题 | 讲解原文 | AI 预估 | 我的评分 | 我的反馈 |
| --- | --- | --- | --- | --- | --- | --- |
"""
ID_PATTERN = re.compile(r"P[0-9]{3,}")


class Entry(NamedTuple):
    id: str
    date: str
    topic: str
    text: str
    estimate: str
    rating: str = "—"
    note: str = ""


def encode(value):
    return html.escape(value, quote=False).replace("|", "&#124;").replace("\n", "<br>")


def decode(value):
    return html.unescape(value.replace("<br>", "\n").replace("<br />", "\n"))


def markdown_row(entry):
    return "| " + " | ".join(map(encode, entry)) + " |"


def load(path):
    original = path.read_bytes() if path.exists() else None
    body = original.decode("utf-8-sig") if original is not None else HEADER
    lines = body.splitlines()
    rows = {}
    for index, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        first, *fields = line.removeprefix("|").removesuffix("|").split("|")
        key = first.strip()
        if not ID_PATTERN.fullmatch(key):
            continue
        if len(fields) != len(Entry._fields) - 1 or key in rows:
            raise ValueError(f"表格第 {index + 1} 行字段数有误或段号重复")
        rows[key] = (index, Entry(key, *(decode(value.strip()) for value in fields)))
    return original, lines, rows


def write(path, original, lines):
    if len(lines) > 2 and lines[2] == OLD_INTRO:
        lines[2] = INTRO
    eol = "\r\n" if original and b"\r\n" in original else "\n"
    new = (eol.join(lines) + eol).encode("utf-8")
    if original and original.startswith(codecs.BOM_UTF8):
        new = codecs.BOM_UTF8 + new
    if new != original:
        save(path, original, new)


def text_field(value):
    if not isinstance(value, str):
        raise ValueError("需要填写文字")
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        raise ValueError("需要填写非空文字")
    return value


def scores(values, *, partial=False):
    if not isinstance(values, list) or len(values) != 3:
        raise ValueError("分数按好懂、读得顺、有趣填写三项")
    for value in values:
        if partial and value is None:
            continue
        if type(value) is not int or not 1 <= value <= 5:
            raise ValueError("分数需为 1 到 5 的整数，暂不打分用 null")
    return values


def overall_score(value):
    if type(value) is not int or not 1 <= value <= 5:
        raise ValueError("整课分数需为 1 到 5 的整数")
    return OVERALL_PREFIX + str(value)


def format_estimate(value):
    if type(value) is int:
        return overall_score(value)
    return "/".join(map(str, scores(value)))


def entry_key(entry):
    return entry.date, entry.topic, entry.text, entry.estimate.startswith(OVERALL_PREFIX)


def append_batch(path, data):
    recorded_on = data["date"]
    if recorded_on != date.fromisoformat(recorded_on).isoformat():
        raise ValueError("日期请用 YYYY-MM-DD")
    topic = text_field(data["topic"])
    paragraphs = data["paragraphs"]
    if not isinstance(paragraphs, list) or not paragraphs:
        raise ValueError("请提供至少一段讲解")
    batch = [
        Entry("", recorded_on, topic, text_field(item["text"]), format_estimate(item["estimate"]))
        for item in paragraphs
    ]
    original, lines, rows = load(path)
    previous = [entry for _, entry in rows.values()]
    signature = [entry_key(entry) for entry in batch]
    history = [entry_key(entry) for entry in previous]
    for index in range(len(history) - len(signature) + 1):
        if history[index:index + len(signature)] == signature:
            return [entry.id for entry in previous[index:index + len(batch)]]

    # Insert at the end of the existing table, retaining any notes the reader adds below.
    table_end = max((index for index, _ in rows.values()), default=-1)
    if table_end == -1:
        table_end = lines.index(HEADER.splitlines()[-1])
    last_id = max((int(key[1:]) for key in rows), default=0)
    current = [entry._replace(id=f"P{last_id + offset:03}") for offset, entry in enumerate(batch, 1)]
    lines[table_end + 1:table_end + 1] = [markdown_row(entry) for entry in current]
    write(path, original, lines)
    return [entry.id for entry in current]


def rate_batch(path, data):
    changes = data["ratings"]
    if not isinstance(changes, list) or not changes:
        raise ValueError("请提供至少一条反馈")
    original, lines, rows = load(path)
    updated = []
    for change in changes:
        key = change["id"]
        if key not in rows:
            raise ValueError(f"找不到段号 {key}")
        index, entry = rows[key]
        if "rating" not in change and "note" not in change:
            raise ValueError(f"{key} 需要评分或感受")
        if "rating" in change:
            if entry.estimate.startswith(OVERALL_PREFIX):
                entry = entry._replace(rating=overall_score(change["rating"]))
            else:
                rating = scores(change["rating"], partial=True)
                existing = entry.rating.split("/") if entry.rating != "—" else ["—"] * 3
                if len(existing) != 3:
                    raise ValueError(f"{key} 的原评分栏应为 3 项")
                entry = entry._replace(rating="/".join(
                    old if value is None else str(value) for old, value in zip(existing, rating)
                ))
        if "note" in change:
            entry = entry._replace(note=text_field(change["note"]))
        rows[key] = (index, entry)
        lines[index] = markdown_row(entry)
        updated.append(key)
    write(path, original, lines)
    return list(dict.fromkeys(updated))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="知识库绝对路径，默认使用本技能配置")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("add", help="从标准输入登记一批讲解与 AI 预估")
    commands.add_parser("rate", help="从标准输入写回用户对原段的反馈")
    recent = commands.add_parser("recent", help="取回最近段落和双方评分")
    recent.add_argument("--limit", type=int, default=6)
    recent.add_argument("--reviewed", action="store_true", help="只看有用户评分或文字反馈的段落")
    args = parser.parse_args()

    try:
        root = args.root or Path(json.loads(SETTINGS.read_text(encoding="utf-8-sig"))["root"])
        if not root.is_absolute() or not root.is_dir():
            raise ValueError("知识库应为已存在的绝对路径")
        root = root.resolve()
        path = checked(root / "教学段落反馈.md", root)
        if args.command == "recent":
            if args.limit < 1:
                raise ValueError("返回数需大于零")
            _, _, rows = load(path)
            entries = [entry._asdict() for _, entry in rows.values()
                       if not args.reviewed or entry.rating not in ("—", "—/—/—") or entry.note]
            result = {"文件": str(path), "段落": entries[-args.limit:]}
        else:
            data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
            action = append_batch if args.command == "add" else rate_batch
            with writer_lock(root):
                result = {"文件": str(path), "段号": action(path, data)}
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"教学反馈记录失败：{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
