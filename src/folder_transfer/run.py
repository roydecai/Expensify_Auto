import argparse
import os
import shutil
import sys
from pathlib import Path


def mirror_and_move(source_dir: Path, dest_dir: Path) -> int:
    if not source_dir.exists():
        print(f"source_dir not found: {source_dir}")
        return 2
    dest_dir.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(source_dir):
        root_path = Path(root)
        rel_path = root_path.relative_to(source_dir)
        target_root = dest_dir / rel_path
        target_root.mkdir(parents=True, exist_ok=True)
        for name in files:
            if not name.lower().endswith(".pdf"):
                continue
            src_file = root_path / name
            dest_file = target_root / name
            if dest_file.exists():
                dest_file.unlink()
            shutil.move(str(src_file), str(dest_file))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir")
    parser.add_argument("dest_dir")
    args = parser.parse_args()
    source_dir = Path(args.source_dir)
    dest_dir = Path(args.dest_dir)
    exit_code = mirror_and_move(source_dir, dest_dir)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
