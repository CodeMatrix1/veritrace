import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Settings:
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_fallback_model: str = "openai/gpt-oss-20b"
    tavily_api_key: str = ""
    fact_check_api_key: str = ""
    database_url: str = ""
    frontend_origin: str = "http://localhost:3000"
    daily_audit_limit: int = 10
    tavily_monthly_credits: int = 1000
    tavily_credit_reserve: int = 300
    tavily_credits_per_search: float = 1.0
    enable_source_history: bool = True
    enable_skeptic: bool = False
    report_reuse_days: int = 7
    policy_version: str = "1"

    @property
    def max_credits_per_audit(self) -> float:
        return MAX_TAVILY_ATTEMPTS_PER_AUDIT * self.tavily_credits_per_search


MAX_INPUT_CHARS = 20_000
MAX_CLAIMS = 3
MAX_TAVILY_ATTEMPTS_PER_AUDIT = 18
RESULTS_PER_SEARCH = 8
MAX_DOCUMENTS_PER_CLAIM = 5
MAX_FETCH_ATTEMPTS_PER_CLAIM = 8
MAX_DOCUMENTS_PER_DOMAIN = 2
MAX_PASSAGES_PER_CLAIM = 8
MAX_PASSAGES_PER_DOCUMENT = 2
MAX_REDIRECTS = 3
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_EXTRACTED_CHARS = 200_000
MAX_PDF_PAGES = 50
MAX_CONCURRENT_AUDITS = 3
MAX_GAP_QUERIES = 2
MAX_QUERY_WORDS = 20
CHAT_SEARCH_DAILY_LIMIT = 50
RETENTION_DAYS = 30


def load_settings() -> Settings:
    return Settings(
        groq_api_key=os.environ.get("GROQ_API_KEY", ""),
        groq_model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
        groq_fallback_model=os.environ.get("GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b"),
        tavily_api_key=os.environ.get("TAVILY_API_KEY", ""),
        fact_check_api_key=os.environ.get("FACT_CHECK_API_KEY", ""),
        database_url=os.environ.get("DATABASE_URL", ""),
        frontend_origin=os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000"),
        daily_audit_limit=int(os.environ.get("DAILY_AUDIT_LIMIT", "10")),
        tavily_monthly_credits=int(os.environ.get("TAVILY_MONTHLY_CREDITS", "1000")),
        tavily_credit_reserve=int(os.environ.get("TAVILY_CREDIT_RESERVE", "300")),
        tavily_credits_per_search=float(os.environ.get("TAVILY_CREDITS_PER_SEARCH", "1")),
        enable_source_history=_env_bool("ENABLE_SOURCE_HISTORY", True),
        enable_skeptic=_env_bool("ENABLE_SKEPTIC", False),
        report_reuse_days=int(os.environ.get("REPORT_REUSE_DAYS", "7")),
        policy_version=os.environ.get("POLICY_VERSION", "1"),
    )
