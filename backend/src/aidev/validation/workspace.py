import io
import tarfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_FILES = 200
MAX_BYTES = 2_000_000


class WorkspaceError(ValueError):
    pass


def _clean(path: str) -> str:
    p = PurePosixPath(path.replace("\\", "/"))
    if p.is_absolute() or not p.parts or any(part in ("", ".", "..") for part in p.parts):
        raise WorkspaceError(f"not a relative path inside the workspace: {path!r}")
    return p.as_posix()


@dataclass(frozen=True)
class Workspace:
    """The files under validation: generated code, its requirements, and its tests.
    Whole files, as `code.delta` events carry them (docs/ui-events.md)."""

    files: Mapping[str, str]

    def __post_init__(self) -> None:
        cleaned = {_clean(path): content for path, content in self.files.items()}
        if len(cleaned) > MAX_FILES:
            raise WorkspaceError(f"{len(cleaned)} files; the limit is {MAX_FILES}")
        size = sum(len(c.encode()) for c in cleaned.values())
        if size > MAX_BYTES:
            raise WorkspaceError(f"{size} bytes; the limit is {MAX_BYTES}")
        object.__setattr__(self, "files", dict(sorted(cleaned.items())))

    @classmethod
    def from_dir(cls, root: Path) -> "Workspace":
        files = {
            p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(root.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts
        }
        return cls(files)

    @property
    def requirements(self) -> str | None:
        return self.files.get("requirements.txt")

    def to_tar(self) -> bytes:
        return tar_files({path: content.encode() for path, content in self.files.items()})


def tar_files(files: Mapping[str, bytes]) -> bytes:
    """A tar stream for `docker run -i … tar -x`: regular files only, owned by the
    sandbox user, no links, so extracting it can't write outside the target."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path, data in files.items():
            info = tarfile.TarInfo(_clean(path))
            info.size = len(data)
            info.mode = 0o644
            info.uid = info.gid = 10001
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()
