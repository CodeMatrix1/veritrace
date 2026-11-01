import re

from config.loader import AppConfig
from schemas.claim import ClaimClassification

# A pattern rather than a list, so official sites count without being named here. It covers
# India and international bodies, as a US or UK agency says nothing about an Indian claim.
OFFICIAL_HOST = re.compile(r"(^|\.)(gov\.in|nic\.in|int)$")
SOCIAL_HOSTS = (
    "x.com", "twitter.com", "facebook.com", "instagram.com", "reddit.com",
    "tiktok.com", "youtube.com", "t.me", "bsky.app", "threads.net", "linkedin.com",
)


def host_matches(host: str, entry: str) -> bool:
    return host == entry or host.endswith(f".{entry}")


def is_social(host: str) -> bool:
    return any(host_matches(host, entry) for entry in SOCIAL_HOSTS)


def is_blocked(host: str, config: AppConfig) -> bool:
    return any(host_matches(host, entry) for entry in config.sources.blocked)


def source_tier(host: str, config: AppConfig) -> str:
    """Return primary for an official site, on any topic."""
    if is_social(host):
        return "unknown"
    if any(host_matches(host, entry) for entry in config.sources.not_primary):
        return "secondary"
    if OFFICIAL_HOST.search(host):
        return "primary"
    listed = any(
        host_matches(host, h) for entry in config.sources.authority for h in entry.hosts
    )
    return "primary" if listed else "unknown"


def official_domains(classification: ClaimClassification, config: AppConfig) -> list[str]:
    hosts: dict[str, None] = {}
    for entry in config.sources.authority:
        if classification.domains & set(entry.claim_domains):
            hosts.update(dict.fromkeys(entry.hosts))
    return list(hosts)
