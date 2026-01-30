from __future__ import annotations

from abc import ABC
import os
from typing import Any, Dict, List, Optional, Tuple, Type, ClassVar
from pathlib import Path
from dataclasses import dataclass, field

#
# --- Stage Template Schema Support ---
#

@dataclass(frozen=True)
class StagePromptPart:
    source: str
    label: str | None = None
    required: bool = True
    render_style: str = "markdown"
    content: str | None = None


@dataclass(frozen=True)
class StageResponseFormat:
    type: str = "text"
    must_include_keys: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class StageFailurePolicy:
    on_refusal: str = "record_error"
    on_parse_error: str = "fallback_text"


@dataclass(frozen=True)
class StagePromptSpec:
    parts: List[StagePromptPart] = field(default_factory=list)


@dataclass(frozen=True)
class StageTemplate:
    kind: str = "single"
    purpose: str = ""
    version: str = "1.0"
    inputs_required: List[str] = field(default_factory=list)
    inputs_optional: List[str] = field(default_factory=list)
    response_format: StageResponseFormat = field(default_factory=StageResponseFormat)
    failure_policy: StageFailurePolicy = field(default_factory=StageFailurePolicy)
    prompt: StagePromptSpec = field(default_factory=StagePromptSpec)

from .observability import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class _RegistryEntry:
    cls: Type["_CouncilMetadata"]
    subfolder: Path

@dataclass(frozen=True)
class _CouncilMetadata(ABC):
    """Class to load and manage metadata files."""

    id: str
    name: str
    prompt: str


    _registry: ClassVar[Dict[str, _RegistryEntry]] = {}


    @classmethod
    def register(
        cls,
        kind: str,
        subclass: Type["_CouncilMetadata"],
        subfolder: Path
    ):
        if kind in cls._registry:
            raise ValueError(f"Kind '{kind}' is already registered.")
        cls._registry[kind] = _RegistryEntry(subclass, subfolder)

    @classmethod
    def _read_text(cls, path: Path) -> str:
        if not path.exists():
            msg = f"Missing required file: {path}"
            logger.error(msg)
            raise RuntimeError(msg)
        return path.read_text(encoding="utf-8")

    @classmethod
    def _parse_toml_front_matter(
        cls,
        md_text: str, 
        *, 
        source: str, 
        require: bool = True
    ) -> Tuple[Dict[str, Any], str]:
        """Parse TOML front matter delimited by +++ ... +++ in a markdown file.

        If `require` is False and the file does not start with +++, returns ({}, full_text).

        Returns: (front_matter_dict, body_text)
        """
        # Normalize common file artifacts.
        if md_text.startswith("\ufeff"):
            md_text = md_text.lstrip("\ufeff")
        lines = md_text.splitlines()

        # Optional front matter support (useful for simple persona markdown files).
        # Be forgiving: allow leading blank lines before the +++ delimiter.
        first_nonempty: Optional[int] = None
        for i, line in enumerate(lines):
            if line.strip() != "":
                first_nonempty = i
                break

        if first_nonempty is None or lines[first_nonempty].strip() != "+++":
            if require:
                msg = (
                    f"CONFIG ERROR: {source} is missing TOML front matter start delimiter (+++)."
                )
                logger.error(msg)
                raise RuntimeError(msg)
            # No front matter; treat whole file as body.
            return {}, md_text.lstrip("\n")

        end_idx: Optional[int] = None
        for i in range((first_nonempty or 0) + 1, len(lines)):
            if lines[i].strip() == "+++":
                end_idx = i
                break

        if end_idx is None:
            msg = f"CONFIG ERROR: {source} is missing TOML front matter end delimiter (+++)."
            logger.error(msg)
            raise RuntimeError(msg)

        toml_text = "\n".join(lines[(first_nonempty or 0) + 1:end_idx]).strip()
        body_text = "\n".join(lines[end_idx + 1 :]).lstrip("\n")

        # Python 3.11+ has tomllib in stdlib.
        try:
            import tomllib  # type: ignore
        except Exception as e:  # pragma: no cover
            msg = f"CONFIG ERROR: Unable to import tomllib for TOML parsing: {e}"
            logger.error(msg)
            raise RuntimeError(msg)

        try:
            data = tomllib.loads(toml_text) if toml_text else {}
        except Exception as e:
            msg = f"CONFIG ERROR: TOML parse error in {source}: {e}"
            logger.error(msg)
            raise RuntimeError(msg)

        return data, body_text

    @classmethod
    def _get_root_dir(cls) -> Path:
        # Allow the metadata root to be configured for different deployments/layouts.
        root = os.environ.get("COUNCIL_METADATA_ROOT")
        if root:
            return Path(root).expanduser().resolve()
        return (Path(__file__).parent.parent / "council-metadata").resolve()
    
    @classmethod
    def _get_metadata_path(cls, registry_entry:_RegistryEntry, id:str) -> Path:
        kind_folder = cls._get_root_dir() / registry_entry.subfolder

        path = (kind_folder / id).with_suffix(".md")

        if not path.exists():
            msg = f"Missing required file: {path}"
            logger.error(msg)
            raise RuntimeError(msg)

        return path


    @classmethod
    def from_file(cls, kind: str, id: str) -> "_CouncilMetadata":
        if kind is None:
            raise ValueError("kind is required")
        registry_entry = cls._registry.get(kind)
        if registry_entry is None:
            raise ValueError(f"Unknown kind: {kind}")
        subclass = registry_entry.cls
        if subclass is None:
            raise ValueError(f"Unknown kind: {kind}")
        path = cls._get_metadata_path(registry_entry, id)
        md = cls._read_text(path)
        fm, body = cls._parse_toml_front_matter(md, source=str(path))

        # Ensure the instance id is tied to the file identifier we loaded.
        fm["id"] = id

        # Special handling for stage files with TOML prompt table (don't clobber prompt)
        if kind == "stage" and isinstance(fm.get("prompt"), dict):
            fm["template_prompt"] = fm.pop("prompt")
            fm["prompt"] = body
        else:
            fm["prompt"] = body

        # Tolerant schema: ignore unknown keys but log them for visibility.
        allowed = set(getattr(subclass, "__dataclass_fields__", {}).keys())
        extra_keys = sorted([k for k in fm.keys() if k not in allowed])
        if extra_keys:
            logger.warning(
                "Ignoring unknown metadata keys",
                extra={
                    "source": str(path),
                    "kind": kind,
                    "id": id,
                    "extra_keys": extra_keys,
                },
            )

        filtered = {k: v for k, v in fm.items() if k in allowed}
        return subclass(**filtered)

    
@dataclass(frozen=True)
class CouncilMetadata(_CouncilMetadata):
    chairman: str
    members: List[str]
    stages: List[str]

_CouncilMetadata.register("council", CouncilMetadata, Path("councils"))


@dataclass(frozen=True)
class PersonaMetadata(_CouncilMetadata):
    """A reusable system prompt persona."""

_CouncilMetadata.register("persona", PersonaMetadata, Path("personas"))


@dataclass(frozen=True)
class MemberMetadata(_CouncilMetadata):
    model_id: str
    persona: str

_CouncilMetadata.register("member", MemberMetadata, Path("members"))



@dataclass(frozen=True)
class StageMetadata(_CouncilMetadata):
    kind: str = "single"
    purpose: str = ""
    version: str = "1.0"
    inputs_required: List[str] = field(default_factory=list)
    inputs_optional: List[str] = field(default_factory=list)
    response_format: Dict[str, Any] = field(default_factory=dict)
    failure_policy: Dict[str, Any] = field(default_factory=dict)
    template_prompt: Dict[str, Any] = field(default_factory=dict)

_CouncilMetadata.register("stage", StageMetadata, Path("stages"))


@dataclass(frozen=True)
class ChairmanMetadata(_CouncilMetadata):
    model_id: str
    persona: str

_CouncilMetadata.register("chairman", ChairmanMetadata, Path("chairmen"))


@dataclass(frozen=True)
class Prompt(_CouncilMetadata):
    pass
_CouncilMetadata.register("prompt", Prompt, Path("prompts"))