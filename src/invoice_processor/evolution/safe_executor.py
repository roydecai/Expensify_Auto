import json
import shutil
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from config import get_project_root


class SafeFileExecutor:
    def __init__(
        self,
        allowed_roots: Optional[List[Path]] = None,
        backup_root: Optional[Path] = None,
    ) -> None:
        project_root = get_project_root()
        self.allowed_roots = [
            root.resolve()
            for root in (
                allowed_roots
                or [
                    project_root / "src" / "invoice_processor",
                ]
            )
        ]
        self.backup_root = (backup_root or (project_root / "backup" / "autofix")).resolve()

    def create_run_id(self) -> str:
        return time.strftime("%Y%m%d_%H%M%S")

    def _is_allowed(self, path: Path) -> bool:
        resolved = path.resolve()
        for root in self.allowed_roots:
            if resolved.is_relative_to(root):
                return True
        return False

    def _ensure_allowed(self, path: Path) -> None:
        if not self._is_allowed(path):
            raise ValueError(f"Path not allowed: {path}")

    def _manifest_path(self, run_id: str) -> Path:
        return self.backup_root / run_id / "manifest.json"

    def backup_files(self, files: Iterable[Path], run_id: str) -> List[Path]:
        backup_dir = self.backup_root / run_id
        backup_dir.mkdir(parents=True, exist_ok=True)
        copied: List[Path] = []
        manifest: Dict[str, List[Dict[str, str]]] = {"files": []}
        for file_path in files:
            self._ensure_allowed(file_path)
            if not file_path.exists():
                continue
            rel_path = file_path.resolve().relative_to(self.allowed_roots[0])
            target_path = backup_dir / rel_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, target_path)
            copied.append(file_path)
            manifest["files"].append(
                {
                    "source": str(file_path.resolve()),
                    "backup": str(target_path.resolve()),
                }
            )
        manifest_path = self._manifest_path(run_id)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        return copied

    def apply_json(self, file_path: Path, payload: object, run_id: str) -> None:
        self.backup_files([file_path], run_id)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def apply_text(self, file_path: Path, content: str, run_id: str) -> None:
        self.backup_files([file_path], run_id)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

    def restore(self, run_id: str) -> None:
        manifest_path = self._manifest_path(run_id)
        if not manifest_path.exists():
            return
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        for item in manifest.get("files", []):
            source = Path(item.get("source", ""))
            backup = Path(item.get("backup", ""))
            if not source or not backup.exists():
                continue
            self._ensure_allowed(source)
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, source)
