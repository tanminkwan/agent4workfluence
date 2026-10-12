"""타겟마다 Git 저장소 하나에 명세를 보관한다. 커밋 하나 = 버전 하나 (설계 8.1절)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from a4w_core.errors import StorageError

from ..ports import SpecArchive

# 저장소 안 표시용 고정 식별자 (설정 대상 아님, 설계 8.1절)
AUTHOR_NAME = "a4w-detection"
AUTHOR_EMAIL = "a4w-detection@localhost"


class GitSpecArchive(SpecArchive):
    def __init__(self, root: Path | str, file_name: str):
        self._root = Path(root)
        self._file = file_name

    def _repo(self, target_id: str) -> Path:
        return self._root / "targets" / target_id / "specs"

    def _git(self, repo: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
        cmd = [
            "git", "-C", str(repo),
            "-c", f"safe.directory={repo}",
            "-c", f"user.name={AUTHOR_NAME}", "-c", f"user.email={AUTHOR_EMAIL}",
            "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false",
            *args,
        ]
        try:
            res = subprocess.run(cmd, input=input_bytes, capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError) as e:
            raise StorageError(f"git {args[0]} 실패: {type(e).__name__}") from None
        return res.stdout

    def _ensure(self, target_id: str) -> Path:
        repo = self._repo(target_id)
        if not (repo / ".git").exists():
            try:
                repo.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise StorageError(f"보관소 디렉터리를 만들 수 없다: {type(e).__name__}") from None
            self._git(repo, "init", "-q")
            # 원문을 바이트 그대로 두기 위해 줄바꿈 변환을 끈다
            (repo / ".git" / "info").mkdir(parents=True, exist_ok=True)
            (repo / ".git" / "info" / "attributes").write_text("* -text\n")
        return repo

    def _head(self, repo: Path) -> str | None:
        try:
            return self._git(repo, "rev-parse", "--verify", "-q", "HEAD").decode().strip() or None
        except StorageError:
            return None

    def commit(self, target_id: str, raw: bytes, version: str) -> str:
        repo = self._ensure(target_id)
        try:
            (repo / self._file).write_bytes(raw)
        except OSError as e:
            raise StorageError(f"명세 파일을 쓸 수 없다: {type(e).__name__}") from None
        self._git(repo, "add", "--", self._file)
        head = self._head(repo)
        if head is not None:
            changed = subprocess.run(
                ["git", "-C", str(repo), "-c", f"safe.directory={repo}", "diff", "--cached", "--quiet"],
                capture_output=True,
            ).returncode
            if changed == 0:
                return head
        self._git(repo, "commit", "-q", "-m", f"spec {version}")
        head = self._head(repo)
        if head is None:
            raise StorageError("커밋 뒤 HEAD를 읽을 수 없다")
        return head

    def read(self, target_id: str, commit: str) -> bytes:
        return self._git(self._repo(target_id), "show", f"{commit}:{self._file}")
