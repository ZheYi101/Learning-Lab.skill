"""Write one learning observation or preference from a UTF-8 JSON object on stdin."""

import argparse
import codecs
from contextlib import contextmanager
from datetime import date
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import sys
import tempfile
import time
from knowledge import EVIDENCE, EXPERIENCES, SETTINGS, STATES, experience_targets, normalized, prose


GROUPS = {"经历", "能力", "偏好"}
STATUSES = {"明确", "试用", "停用"}
WITHDRAWN = "此前的教学借用已撤回，新的可迁移例子待确认。"
COMMON = {"group", "key", "date", "source", "text", "reason", "event_id"}
FIELDS = {
    "经历": {"title", "kind", "cues", "teaching", "refs"},
    "能力": {"title", "state", "basis", "cues", "teaching", "refs", "experiences"},
    "偏好": {"status"},
}


def single_line(value):
    if not isinstance(value, str) or not value.strip() or len(value.splitlines()) != 1:
        raise ValueError("字段应为单行非空文字")
    if any(ord(c) < 32 for c in value) or "<!-- learning-lab:" in value:
        raise ValueError("字段含保留字符")
    return value.strip()


def validate(data):
    if not isinstance(data, dict) or not COMMON.intersection(data):
        raise ValueError("需要一个 JSON 对象")
    group = data.get("group")
    if group not in GROUPS:
        raise ValueError("group 请使用经历、能力或偏好")
    unknown = data.keys() - COMMON - FIELDS[group]
    if unknown:
        raise ValueError(f"无法识别的字段 {', '.join(sorted(unknown))}")
    for name in ("key", "date", "source", "text"):
        if name not in data:
            raise ValueError(f"缺少 {name}")
    out = {}
    for name, value in data.items():
        if name in ("refs", "cues", "experiences"):
            if not isinstance(value, list):
                raise ValueError(f"{name} 应为文字数组")
            out[name] = list(dict.fromkeys(single_line(v) for v in value))
        elif name == "teaching" and value == "":
            out[name] = WITHDRAWN
        else:
            out[name] = single_line(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", out["date"]):
        raise ValueError("date 请使用 YYYY-MM-DD")
    date.fromisoformat(out["date"])
    key = out["key"]
    if "｜" in key or "｜" in out["source"]:
        raise ValueError("key 和 source 不能包含分隔符 ｜")
    if group != "偏好":
        win_key = normalized(key)
        if (re.search(r'[/\\:*?"<>|]', win_key) or win_key.endswith((".", " "))
                or re.fullmatch(r"(con|nul|prn|aux|conin\$|conout\$|(?:com|lpt)[0-9])(?:\..*)?", win_key)):
            raise ValueError("key 应为安全的文件名，不含目录或平台保留字符")
        if any(re.search(r"[\s#]", cue) for cue in out.get("cues", [])):
            raise ValueError("每个线索应为不含空格或 # 的标签")
    for name, allowed in (("kind", EXPERIENCES), ("state", STATES), ("basis", EVIDENCE), ("status", STATUSES)):
        if name in out and out[name] not in allowed:
            raise ValueError(f"{name} 可用值为 {'、'.join(sorted(allowed))}")
    return out


def checked(path, root):
    if not path.resolve().is_relative_to(root) or path.is_symlink():
        raise ValueError("路径未指向知识库内的普通文件或目录")
    return path


def experience_links(root, note, names):
    if not names:
        return {}
    folder = checked(root / "经历", root)
    if not folder.is_dir():
        raise ValueError("找不到经历目录")
    by_file, by_title = {}, {}
    for path in folder.rglob("*.md"):
        if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
            continue
        with path.open(encoding="utf-8-sig") as stream:
            first = next((l.strip() for l in stream if l.strip()), "")
        title = first[2:] if first.startswith("# ") else path.stem
        rel = path.relative_to(folder).as_posix()
        for key in {normalized(v) for v in (rel, rel[:-3], path.name, path.stem)}:
            by_file.setdefault(key, []).append((path, title))
        by_title.setdefault(normalized(title), []).append((path, title))
    result = {}
    for name in names:
        value = name.replace("\\", "/")
        parts = PureWindowsPath(value)
        if parts.anchor or ".." in parts.parts:
            raise ValueError("经历关联只接受库内已有经历")
        key = normalized(value)
        candidates = by_file.get(key) or by_title.get(key) or []
        if len(candidates) != 1:
            raise ValueError(f"经历名称未匹配到唯一条目：{name}")
        path, title = candidates[0]
        relative = Path(os.path.relpath(path, note.parent)).as_posix()
        url = relative.replace("%", "%25").replace("#", "%23").replace("?", "%3F").replace(">", "%3E").replace("<", "%3C")
        label = re.sub(r"([\[\]\\])", r"\\\1", title)
        result[path] = f"[{label}](<{url}>)"
    return result


def missing_links(value, note, root, links):
    """Compare destinations while keeping existing prose, labels and spelling untouched."""
    destinations = set()
    for _, target, wiki_link in experience_targets(value):
        if not wiki_link:
            if target and "://" not in target:
                destinations.add(Path(os.path.abspath(note.parent / target)))
            continue
        wiki = target
        if not wiki.casefold().endswith(".md"):
            wiki += ".md"
        if wiki.startswith(root.name + "/"):
            wiki = wiki[len(root.name)+1:]
        destinations.add(Path(os.path.abspath(root / wiki)))
        destinations.add(Path(os.path.abspath(note.parent / wiki)))
        if "/" not in wiki:
            candidates = [p for p in (root / "经历").rglob("*.md") if normalized(p.name) == normalized(wiki)]
            if len(candidates) == 1:
                destinations.add(candidates[0])
    return [link for path, link in links.items() if path not in destinations]


def locate(root, entry):
    if entry["group"] == "偏好":
        return checked(root / "学习偏好.md", root)
    folder = checked(root / entry["group"], root)
    if not folder.is_dir():
        raise ValueError(f"找不到目录 {folder}")
    key = entry["key"]
    target = checked(folder / f"{key}.md", folder.resolve())
    wanted = {normalized(key), normalized(entry.get("title", key))}
    matches = []
    stable = []
    for path in folder.rglob("*.md"):
        if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
            continue
        if normalized(path.stem) == normalized(key):
            stable.append(path)
            continue
        with path.open(encoding="utf-8-sig") as stream:
            first = next((l.strip() for l in stream if l.strip()), "")
        if normalized(path.stem) in wanted or (first.startswith("# ") and normalized(first[2:]) in wanted):
            matches.append(path)
    matches = stable or matches
    if len(matches) > 1:
        raise ValueError("多个条目匹配给定名称，请先核对稳定键")
    return matches[0] if matches else target


@contextmanager
def writer_lock(root):
    # Keep coordination files out of the synced Markdown vault. A process exit releases the OS lock.
    digest = hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest()
    lock_path = Path(tempfile.gettempdir()) / f"learning-lab-{digest}.lock"
    with lock_path.open("a+b") as stream:
        if not stream.seek(0, os.SEEK_END):
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            def acquire():
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            def release():
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(stream, fcntl.LOCK_UN)
        end = time.monotonic() + 12
        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic() >= end:
                    raise TimeoutError("知识库正在写入，请稍后重试")
                time.sleep(0.05)
        try:
            yield
        finally:
            release()


def append_to_section(lines, name, values):
    header = f"## {name}"
    start = next((i+1 for i,l in prose(lines) if l == header), None)
    if start is None:
        lines.extend(["", header, ""])
        start = len(lines)-1
    end = next((i for i,l in prose(lines) if i >= start and l.startswith(("# ", "## "))), len(lines))
    while end > start and (not lines[end-1].strip() or lines[end-1].startswith("<!-- learning-lab:event ")):
        end -= 1
    values = [v for v in values if v not in lines[start:end]]
    if values:
        lines[end:end] = ([""] if end == start else []) + values


def tag_value(tags, prefix):
    return next((t[len(prefix):] for t in tags if t.startswith(prefix)), "")


def update_tags(tags, data):
    result = list(tags)
    if data["group"] == "经历":
        updates = {"#类型/": [data.get("kind", tag_value(tags, "#类型/"))]}
        if updates["#类型/"][0] not in EXPERIENCES:
            raise ValueError("新建或修复经历要给出 kind")
    else:
        state = data.get("state", tag_value(tags, "#状态/"))
        basis = data.get("basis", tag_value(tags, "#依据/"))
        if state not in STATES or basis not in EVIDENCE:
            raise ValueError("新建或修复能力要给出 state 和 basis")
        if basis == "材料" and state != "待确认":
            raise ValueError("仅有材料的能力状态应为待确认")
        updates = {"#状态/": [state], "#依据/": [basis]}
    if "cues" in data:
        updates["#线索/"] = data["cues"]
    for prefix, values in updates.items():
        old = [tag for tag in result if tag.startswith(prefix)]
        new = [prefix + value for value in values]
        if old != new:
            at = result.index(old[0]) if old else len(result)
            result = [tag for tag in result if not tag.startswith(prefix)]
            result[at:at] = new
    return result


def prior_date(value):
    match = re.match(r"(\d{4}-\d{2}-\d{2})(?:\s|｜|$)", value)
    return match[1] if match else ""


def write_note(lines, data, stamp, path, root, links):
    head = next((i for i,l in enumerate(lines) if l.strip()), len(lines))
    if head == len(lines) or not lines[head].startswith("# "):
        lines.insert(head, "# " + data.get("title", data["key"]))
    tag_at = next((i for i in range(head+1, len(lines)) if lines[i].strip()), len(lines))
    if tag_at == len(lines) or not all(t.startswith("#") and t != "#" for t in lines[tag_at].split()):
        tag_at = head + 1
        lines.insert(tag_at, "")
    old_tags = lines[tag_at].split()
    tags = update_tags(old_tags, data)
    label = "- 最近记录｜"
    pos = next((i for i,l in prose(lines) if l.startswith(label)), None)
    old = lines[pos][len(label):] if pos is not None else ""
    observed = stamp + "｜" + data["text"]
    if prior_date(old) > data["date"]:
        append_to_section(lines, "以前的记录", ["- " + observed + " " + " ".join(tags)])
        if data.get("teaching"):
            append_to_section(lines, "以前的记录", ["- 当时的教学借用｜" + stamp + "｜" + data["teaching"]])
        if links:
            append_to_section(lines, "以前的记录", ["- 当时关联的经历｜" + stamp + "｜" + " · ".join(links.values())])
    else:
        if old and (old != observed or tags != old_tags):
            append_to_section(lines, "以前的记录", ["- " + old + " " + " ".join(old_tags)])
        if tags != old_tags:
            lines[tag_at] = " ".join(tags)
        if pos is None:
            pos = tag_at + 1
            lines[pos:pos] = ["", label + observed]
            pos += 1
        else:
            lines[pos] = label + observed
        if "teaching" in data or "reason" in data:
            field = "- 教学借用｜"
            at = next((i for i,l in prose(lines) if l.startswith(field)), None)
            value = field + data.get("teaching", WITHDRAWN)
            if at is None:
                lines.insert(pos + 1, value)
            elif lines[at] != value:
                previous = lines[at][len(field):]
                lines[at] = value
                append_to_section(lines, "以前的记录", ["- 此前的教学借用｜" + previous])
        if links:
            field = "- 相关经历｜"
            rows = [i for i,l in prose(lines) if l.startswith(field)]
            additions = missing_links("\n".join(lines[i] for i in rows), path, root, links)
            if additions and rows:
                sep = " · " if lines[rows[0]][len(field):].strip() else ""
                lines[rows[0]] += sep + " · ".join(additions)
            elif additions:
                lines.insert(pos + 1, field + " · ".join(additions))
    if data.get("refs"):
        append_to_section(lines, "依据", ["- " + ref for ref in data["refs"]])


def write_preference(lines, data, stamp):
    if not lines:
        lines.extend(["# 学习偏好", ""])
    prefix = "- 偏好｜" + data["key"] + "｜"
    matches = [i for i,l in prose(lines) if l.startswith(prefix)]
    if len(matches) > 1:
        raise ValueError("同一偏好键存在多条当前记录，请先核对")
    pos = matches[0] if matches else None
    old = lines[pos] if pos is not None else ""
    fields = old[len(prefix):].split("｜", 2)
    if old and (len(fields) != 3 or fields[0] not in STATUSES):
        raise ValueError("现有偏好行格式无法识别")
    status = data.get("status", fields[0] if old else "明确")
    current = prefix + status + "｜" + stamp + "｜" + data["text"]
    if pos is None:
        # Keep current values above history sections and event markers.
        at = next((i for i,l in enumerate(lines) if l.startswith(("## 以前的记录", "<!-- learning-lab:event "))), len(lines))
        lines.insert(at, current)
    elif current != old:
        historical = current if prior_date(fields[1]) > data["date"] else old
        if historical == old:
            lines[pos] = current
        append_to_section(lines, "以前的记录", [historical.replace("- 偏好｜", "- 以前的偏好｜", 1)])


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def save(path, old, new):
    mode = stat.S_IMODE(path.stat().st_mode) if old is not None else None
    tmp = tempfile.NamedTemporaryFile(dir=path.parent, prefix=".learning-lab-", suffix=".tmp", delete=False)
    scratch = Path(tmp.name)
    try:
        with tmp:
            tmp.write(new)
            tmp.flush()
            os.fsync(tmp.fileno())
        if mode is not None:
            scratch.chmod(mode)
        if (path.read_bytes() if path.exists() else None) != old:
            raise OSError("条目已被其他编辑器修改，请重试以合并最新内容")
        os.replace(scratch, path)
    finally:
        scratch.unlink(missing_ok=True)


def record(root, data):
    data = validate(data)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("知识库应为已存在的绝对目录")
    root = root.resolve()
    with writer_lock(root):
        path = locate(root, data)
        old = path.read_bytes() if path.exists() else None
        links = experience_links(root, path, data.get("experiences", []))
        content = (old or b"").decode("utf-8-sig")
        lines = content.splitlines()
        # The file is the stable identity, even if it was located using its display title.
        payload = {**data, "key": data["key"] if data["group"] == "偏好" else path.stem}
        if "experiences" in payload:
            payload["experiences"] = sorted(p.relative_to(root).as_posix() for p in links)
        payload_hash = digest({k:v for k,v in payload.items() if k != "event_id"})
        event_hash = digest([payload["group"], payload["key"], payload.get("event_id", payload_hash)])
        marker = f"<!-- learning-lab:event {event_hash} {payload_hash} -->"
        if any(l.startswith(f"<!-- learning-lab:event {event_hash} ") for l in lines):
            if marker not in lines:
                raise ValueError("event_id 已用于不同内容；纠正请使用新事件")
            return {"结果": "无变化", "文件": str(path)}
        stamp = data["date"] + " " + data["source"]
        if data["group"] == "偏好":
            write_preference(lines, data, stamp)
        else:
            write_note(lines, data, stamp, path, root, links)
        if "reason" in data:
            append_to_section(lines, "以前的记录", ["- 更正说明｜" + stamp + "｜" + data["key"] + "｜" + data["reason"]])
        lines.extend(["", marker])
        newline = re.search(r"\r\n|\n|\r", content)
        eol = newline.group() if newline else "\n"
        trailing = old is None or content.endswith(("\r", "\n"))
        encoded = (eol.join(lines) + (eol if trailing else "")).encode("utf-8")
        if old and old.startswith(codecs.BOM_UTF8):
            encoded = codecs.BOM_UTF8 + encoded
        save(path, old, encoded)
        return {"结果": "新建" if old is None else "已更新", "文件": str(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="使用指定的本地知识库目录")
    args = parser.parse_args()
    try:
        root = args.root or Path(json.loads(SETTINGS.read_text(encoding="utf-8-sig"))["root"])
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        result = record(root, data)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"写入个人知识库失败：{error}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
