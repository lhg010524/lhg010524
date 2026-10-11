#!/usr/bin/env python3
"""
중복 파일 찾기 / 정리 스크립트 (macOS)

내용이 완전히 같은 파일(SHA-256 해시 동일)만 중복으로 판단합니다.
이름이 같아도 내용이 다르면 건드리지 않습니다.

기본 동작은 "미리보기"(아무것도 안 지움)입니다.
--delete 를 붙여야 실제로 정리하며, 이때도 바로 삭제하지 않고
휴지통(~/.Trash)으로 옮기기 때문에 Finder에서 되돌릴 수 있습니다.

사용법:
    python3 find_duplicates.py                      # 문서 + 다운로드 미리보기
    python3 find_duplicates.py --delete             # 중복본을 휴지통으로 이동
    python3 find_duplicates.py ~/Desktop ~/Pictures # 다른 폴더 검사
    python3 find_duplicates.py --min-size 1M        # 1MB 이상 파일만

남길 파일 고르는 기준 (그룹마다 1개만 남김):
    1. 앞에 적은 폴더에 있는 파일 우선 (기본: 문서 > 다운로드)
    2. 이름에 "(1)", " 2", " 복사본", " copy" 같은 사본 표시가 없는 파일 우선
    3. 더 오래된(먼저 생긴) 파일 우선
"""
import argparse
import hashlib
import os
import re
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

DEFAULT_DIRS = ["~/Documents", "~/Downloads"]
SKIP_NAMES = {".DS_Store", ".localized", "Icon\r"}
# 앱 번들, 사진 보관함 등 '폴더처럼 보이는 패키지'는 안에 들어가지 않음
SKIP_DIR_SUFFIXES = (".app", ".photoslibrary", ".musiclibrary", ".tvlibrary",
                     ".fcpbundle", ".logicx", ".band", ".pages", ".numbers",
                     ".key", ".rtfd", ".xcodeproj", ".git")
COPY_PATTERN = re.compile(r"(\s\(\d+\)|\s\d+|\s복사본(\s\d+)?|\scopy(\s\d+)?|-\d+)$",
                          re.IGNORECASE)


def parse_size(text):
    units = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMG]?)B?", text.strip().upper())
    if not m:
        raise argparse.ArgumentTypeError(f"크기 형식 오류: {text} (예: 500K, 1M)")
    return int(float(m.group(1)) * units[m.group(2)])


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024
    return f"{n:.1f}TB"


def walk_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and not d.endswith(SKIP_DIR_SUFFIXES)]
        for name in filenames:
            if name in SKIP_NAMES or name.startswith("._"):
                continue
            p = Path(dirpath) / name
            if p.is_symlink() or not p.is_file():
                continue
            yield p


def file_hash(path, first_chunk_only=False):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        if first_chunk_only:
            h.update(f.read(64 * 1024))
        else:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    return h.hexdigest()


def find_duplicates(roots, min_size):
    by_size = defaultdict(list)
    seen_inodes = set()
    for root in roots:
        for p in walk_files(root):
            try:
                st = p.stat()
            except OSError:
                continue
            key = (st.st_dev, st.st_ino)
            if key in seen_inodes:  # 하드링크/같은 폴더 중복 지정 방지
                continue
            seen_inodes.add(key)
            if st.st_size >= min_size:
                by_size[st.st_size].append(p)

    groups = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        # 1차: 앞부분 64KB 해시로 빠르게 거르고, 2차: 전체 해시로 확정
        by_quick = defaultdict(list)
        for p in paths:
            try:
                by_quick[file_hash(p, first_chunk_only=True)].append(p)
            except OSError:
                pass
        for candidates in by_quick.values():
            if len(candidates) < 2:
                continue
            by_full = defaultdict(list)
            for p in candidates:
                try:
                    by_full[file_hash(p)].append(p)
                except OSError:
                    pass
            groups.extend((size, g) for g in by_full.values() if len(g) > 1)
    return groups


def keep_priority(path, roots):
    root_rank = next((i for i, r in enumerate(roots)
                      if str(path).startswith(str(r) + os.sep)), len(roots))
    looks_like_copy = bool(COPY_PATTERN.search(path.stem))
    try:
        st = path.stat()
        born = getattr(st, "st_birthtime", st.st_mtime)
    except OSError:
        born = float("inf")
    return (root_rank, looks_like_copy, born, len(str(path)))


def move_to_trash(path):
    trash = Path.home() / ".Trash"
    trash.mkdir(exist_ok=True)
    target = trash / path.name
    if target.exists():
        target = trash / f"{path.stem} {time.strftime('%H.%M.%S')}{path.suffix}"
    shutil.move(str(path), str(target))


def main():
    ap = argparse.ArgumentParser(description="중복 파일 찾기 / 휴지통으로 정리")
    ap.add_argument("folders", nargs="*", default=DEFAULT_DIRS,
                    help="검사할 폴더 (앞에 적을수록 원본으로 남길 우선순위 높음)")
    ap.add_argument("--delete", action="store_true",
                    help="실제로 중복본을 휴지통으로 이동 (없으면 미리보기만)")
    ap.add_argument("--min-size", type=parse_size, default=1,
                    help="이보다 작은 파일은 무시 (기본 1B = 빈 파일 제외)")
    args = ap.parse_args()

    roots = []
    for f in args.folders:
        r = Path(os.path.expanduser(f)).resolve()
        if not r.is_dir():
            print(f"폴더 없음, 건너뜀: {r}", file=sys.stderr)
            continue
        roots.append(r)
    if not roots:
        sys.exit("검사할 폴더가 없습니다.")

    print("검사 중:", ", ".join(str(r) for r in roots))
    groups = find_duplicates(roots, args.min_size)
    if not groups:
        print("중복 파일이 없습니다. 🎉")
        return

    groups.sort(key=lambda g: g[0] * (len(g[1]) - 1), reverse=True)
    total_files = total_bytes = 0
    for size, paths in groups:
        paths.sort(key=lambda p: keep_priority(p, roots))
        keep, dupes = paths[0], paths[1:]
        print(f"\n[{human(size)} × {len(paths)}]")
        print(f"  남김  {keep}")
        for d in dupes:
            print(f"  삭제  {d}")
            total_files += 1
            total_bytes += size
            if args.delete:
                try:
                    move_to_trash(d)
                except OSError as e:
                    print(f"    ⚠️  이동 실패: {e}")

    print("\n" + "-" * 50)
    print(f"중복 그룹 {len(groups)}개, 정리 대상 {total_files}개, 확보 용량 {human(total_bytes)}")
    if args.delete:
        print("휴지통으로 옮겼습니다. 확인 후 휴지통을 비우면 용량이 확보됩니다.")
    else:
        print("※ 미리보기입니다. 실제로 정리하려면 --delete 를 붙여 다시 실행하세요.")


if __name__ == "__main__":
    main()
