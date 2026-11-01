from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from schemas.claim import ClaimType, Domain

CONFIG_DIR = Path(__file__).parent


class EvidenceRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_required: bool
    minimum_independent_sources: int | None = Field(default=None, ge=1)
    secondary_sufficient: bool = False

    @model_validator(mode="after")
    def requires_a_standard(self) -> "EvidenceRule":
        if not self.primary_required and self.minimum_independent_sources is None:
            raise ValueError("primary-preferred types need minimum_independent_sources")
        return self


class EvidenceRules(RootModel[dict[ClaimType, EvidenceRule]]):
    @model_validator(mode="after")
    def covers_all_types(self) -> "EvidenceRules":
        missing = set(ClaimType.__args__) - set(self.root)
        if missing:
            raise ValueError(f"missing rules for claim types: {sorted(missing)}")
        return self


class AuthorityEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hosts: list[str]
    claim_domains: list[Domain]


class SourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    authority: list[AuthorityEntry]
    not_primary: list[str] = Field(default_factory=list)
    blocked: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class AppConfig:
    rules_version: str
    rules: dict[str, EvidenceRule]
    sources: SourcesConfig


def load_config(config_dir: Path = CONFIG_DIR) -> AppConfig:
    """Parse both YAML files. Invalid config raises, which stops startup."""
    raw_rules = yaml.safe_load((config_dir / "evidence_rules.yaml").read_text("utf-8"))
    rules_version = str(raw_rules.pop("version"))
    rules = EvidenceRules.model_validate(raw_rules).root
    raw_sources = yaml.safe_load((config_dir / "sources.yaml").read_text("utf-8"))
    return AppConfig(rules_version, rules, SourcesConfig.model_validate(raw_sources))
