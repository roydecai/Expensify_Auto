import argparse
import os
import shutil
import sys
import json
from pathlib import Path


def get_project_root() -> Path:
    return Path(__file__).parent.parent.parent


def check_status(pdf_name: str, failed_pdfs: set) -> bool:
    """
    Check if the PDF is in the failed list (validation_detail.json).
    Returns True if the PDF is NOT in the failed list (meaning it passed or wasn't flagged).
    """
    # If the PDF is in the failed set, it means it failed validation -> Return False (don't move)
    if pdf_name in failed_pdfs:
        return False
    return True


def get_failed_pdfs() -> set:
    """
    Load validation_detail.json and return a set of PDF filenames that failed.
    """
    project_root = get_project_root()
    # Try both potential locations just in case, prioritizing project root
    candidates = [
        project_root / "temp" / "validation_detail.json",
        project_root / "src" / "invoice_processor" / "temp" / "validation_detail.json"
    ]
    
    failed_pdfs = set()
    
    for json_path in candidates:
        if json_path.exists():
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # "reports" list contains failed items
                    if "reports" in data:
                        for report in data["reports"]:
                            context = report.get("context", {})
                            pdf_filename = context.get("pdf_filename")
                            if pdf_filename:
                                failed_pdfs.add(pdf_filename)
                # Found and processed, stop checking other candidates
                break
            except Exception as e:
                print(f"Error reading {json_path}: {e}")
                
    return failed_pdfs


def mirror_and_move(source_dir: Path, dest_dir: Path) -> int:
    if not source_dir.exists():
        print(f"source_dir not found: {source_dir}")
        return 2
    
    # Pre-load failed PDFs
    failed_pdfs = get_failed_pdfs()
    print(f"Found {len(failed_pdfs)} failed PDFs in validation_detail.json")
    
    dest_dir.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(source_dir):
        root_path = Path(root)
        rel_path = root_path.relative_to(source_dir)
        target_root = dest_dir / rel_path
        
        for name in files:
            if not name.lower().endswith(".pdf"):
                continue
            
            # Check status before moving
            if not check_status(name, failed_pdfs):
                print(f"Skipping {name} (Found in validation_detail.json failures)")
                continue

            target_root.mkdir(parents=True, exist_ok=True)
            src_file = root_path / name
            dest_file = target_root / name
            if dest_file.exists():
                dest_file.unlink()
            shutil.move(str(src_file), str(dest_file))
            print(f"Moved {name} to {dest_file}")
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
