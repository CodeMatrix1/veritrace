from config.loader import AppConfig, EvidenceRule
from schemas.claim import ClaimClassification


def requirements_for(classification: ClaimClassification, config: AppConfig) -> EvidenceRule:
    return config.rules[classification.claim_type]
