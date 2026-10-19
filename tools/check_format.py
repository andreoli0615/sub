#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查 .ass 对白行的全角/半角用法是否符合组内惯例。

规则（刻意只收「跨项目共识」的两条，避免各项目自定写法造成误报）：

  1) 对白行里出现全角空格 U+3000：断句应统一使用半角空格；
  2) 中文对白行里出现半角 ? 或 !：应使用全角 ？！

**不检查**的部分（有意为之）：
  - 是否使用逗号/顿号/句号：各项目写法不同（有的项目对白不加标点，有的
    项目在歌词/注释/标题里特意使用标点），一概不判。
  - 歌词、注释、标题、声明、场面文本等非对白样式：一律跳过。

本检查只「提醒」、不拦截合并（始终以 0 退出）。

两种模式：

  --diff        从 stdin 读 `git diff -U0` 的输出，只检查本次新增 (+) 的对白行。
                输出：①GitHub 行内警告 (::warning)；②运行摘要 (GITHUB_STEP_SUMMARY)；
                ③供 PR 评论用的 Markdown（写到 FORMAT_COMMENT_FILE，默认
                format_comment.md，仅有命中时写）；④命中数写到 GITHUB_OUTPUT。
                CI 用这个。

  --all [路径]  扫描整份文件（默认当前目录下所有 *.ass），打印 文件:行号: 命中。
                本地自查用。有命中时以 1 退出，方便本地当门槛。
"""
import os
import re
import sys
import urllib.parse

# === 可调开关 ===
DIALOGUE_STYLE = "对白"                  # 只检查样式名含此串的行（对白系）
JOINT = "\u3000"                         # 全角空格
CN_HALF_PUNCT = re.compile(r"[?!]")      # 中文行里的半角问号/叹号
IS_CN = "中文"                           # 样式名含此串视为中文轨

TAG = re.compile(r"\{[^}]*\}")           # ASS 特效标签 {...}
COMMENT_MARKER = "<!-- format-check -->" # 粘性评论标记，供 workflow 找到自己那条


def dialogue_fields(line):
    """若是对白行，返回 (style, 正文)；否则返回 (None, None)。"""
    if not line.startswith("Dialogue:"):
        return None, None
    parts = line.split(",", 9)
    if len(parts) < 10:
        return None, None
    return parts[3], parts[9]


def check_line(line):
    """返回 (命中列表, 用于展示的正文)。命中项为 (编号, 说明)。"""
    style, text = dialogue_fields(line.rstrip("\r\n"))
    if text is None or DIALOGUE_STYLE not in style:
        return [], None
    cleaned = TAG.sub("", text)          # 先剥特效标签，避免 \pos(1.2,3.4) 之类误报
    hits = []
    if JOINT in cleaned:
        hits.append((JOINT, "全角空格：断句请改用半角空格"))
    if IS_CN in style and CN_HALF_PUNCT.search(cleaned):
        found = "".join(sorted(set(CN_HALF_PUNCT.findall(cleaned))))
        hits.append((found, "半角 %s：中文应使用全角 ？ ！" % found))
    return hits, cleaned


def _snippet(text):
    s = (text or "").strip()
    return s[:117] + "..." if len(s) > 120 else s


def _messages(hits, snip):
    out = []
    for found, why in hits:
        msg = "格式提醒：发现 %s。%s。整行：%s" % (
            found.replace("\r", " ").replace("\n", " "), why, snip)
        out.append(msg.replace("\r", " ").replace("\n", " "))
    return out


def run_diff(diff_text):
    """解析 `git diff -U0`，对新增对白行输出注解，并产出摘要/评论/命中数。"""
    path = None
    new_ln = None
    rows = []                 # (path, new_lineno, 说明, 展示正文)
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            p = line[4:].strip()
            path = None if p == "/dev/null" else (p[2:] if p.startswith("b/") else p)
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            new_ln = int(m.group(1)) if m else None
        elif line.startswith("+") and not line.startswith("+++"):
            if path is not None and new_ln is not None:
                hits, cleaned = check_line(line[1:])
                if hits:
                    snip = _snippet(cleaned)
                    for msg in _messages(hits, snip):
                        print("::warning file=%s,line=%d::%s" % (path, new_ln, msg))
                        rows.append((path, new_ln, msg, snip))
                new_ln += 1
        elif line.startswith(" "):
            if new_ln is not None:
                new_ln += 1
        # 以 '-' 开头的行不推进新文件行号

    write_summary(rows)
    write_comment(rows)
    write_output(len(rows))
    sys.stderr.write(
        ("共发现 %d 处格式提醒（不拦截）。\n" % len(rows))
        if rows else "未发现格式问题。\n")
    return 0  # 始终成功：只提醒、不拦合并


def write_summary(rows):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        if not rows:
            f.write("### 格式检查 ✅\n\n本次新增对白未发现全角空格 / 半角 ?! 问题。\n")
            return
        f.write("### 格式检查 ⚠️ 发现 %d 处（提醒，不拦合并）\n\n" % len(rows))
        f.write("> 对白断句用半角空格；中文标点用全角 ？！。\n\n")
        f.write("| 文件 | 行 | 内容 |\n|---|---|---|\n")
        for p, ln, _msg, s in rows:
            f.write("| %s | %d | %s |\n" % (p, ln, s.replace("|", "\\|")))


def write_comment(rows):
    if not rows:
        return
    out = os.environ.get("FORMAT_COMMENT_FILE", "format_comment.md")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    sha = os.environ.get("PR_HEAD_SHA", "")
    lines = [
        COMMENT_MARKER,
        "### ⚠️ 格式提醒（全角空格 / 半角标点）",
        "",
        "本次改动里发现 **%d 处**格式问题。检查范围仅限**对白行**，"
        "规则只覆盖两条共识项：**断句用半角空格**、**中文标点用全角 ？！**。"
        "本提醒**不会阻止合并**。" % len(rows),
        "",
        "| 位置 | 内容 |",
        "|---|---|",
    ]
    for p, ln, msg, s in rows:
        loc = "%s:%d" % (p, ln)
        if repo and sha:
            url = "%s/%s/blob/%s/%s#L%d" % (
                server, repo, sha, urllib.parse.quote(p), ln)
            loc = "[%s:%d](%s)" % (p, ln, url)
        lines.append("| %s | %s |" % (loc, msg.replace("|", "\\|")))
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def write_output(count):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("count=%d\n" % count)


def run_all(paths):
    if not paths:
        paths = []
        for root, dirs, files in os.walk("."):
            if ".git" in root.split(os.sep):
                dirs[:] = [d for d in dirs if d != ".git"]
                continue
            for name in files:
                if name.lower().endswith(".ass"):
                    paths.append(os.path.join(root, name))
    total = 0
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8-sig", errors="ignore") as f:
                for i, line in enumerate(f, 1):
                    hits, cleaned = check_line(line)
                    if hits:
                        total += 1
                        print("%s:%d: %s | %s" % (
                            p, i, "；".join(w for _, w in hits), _snippet(cleaned)))
        except OSError as e:
            sys.stderr.write("跳过 %s: %s\n" % (p, e))
    sys.stderr.write("共发现 %d 处格式问题。\n" % total)
    return 1 if total else 0


def main(argv):
    args = argv[1:]
    if args and args[0] == "--diff":
        return run_diff(sys.stdin.read())
    if args and args[0] == "--all":
        return run_all(args[1:])
    sys.stderr.write(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
