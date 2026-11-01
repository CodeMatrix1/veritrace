import ast
import string
from pathlib import Path

import prompts

BACKEND = Path(__file__).parent.parent
EXPECTED_PLACEHOLDERS = {
    "EXTRACTION_PROMPT": {"reference_date", "feedback", "text"},
    "CLASSIFICATION_PROMPT": {"domains", "claims"},
    "INITIAL_QUERY_PROMPT": {"focus", "exceptions", "max_words", "claim"},
    "GAP_QUERY_PROMPT": {"count", "max_words", "gaps", "tried", "claim"},
    "EVIDENCE_PROMPT": {"claim", "details", "sources"},
    "SKEPTIC_PROMPT": {"claim", "ledger"},
    "SUMMARY_PROMPT": {"results"},
    "CHAT_PROMPT": {"today", "question", "passages"},
    "CHAT_REWRITE_PROMPT": {"history", "question"},
}


def test_prompts_keep_their_placeholders():
    for name, expected in EXPECTED_PLACEHOLDERS.items():
        fields = {f for _, f, _, _ in string.Formatter().parse(getattr(prompts, name)) if f}
        assert fields == expected, name


def test_every_prompt_has_a_version():
    for name in EXPECTED_PLACEHOLDERS:
        version_name = name.replace("_PROMPT", "_PROMPT_VERSION")
        version_name = version_name.replace("INITIAL_QUERY", "QUERY").replace("GAP_QUERY", "QUERY")
        assert getattr(prompts, version_name), name


def test_no_prompt_text_lives_outside_prompts_file():
    for folder in ("components", "pipeline", "services", "adapters", "api"):
        for path in (BACKEND / folder).glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text("utf-8"))):
                if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                    assert "PROMPT" not in node.targets[0].id, f"{path.name}: {node.targets[0].id}"


def test_examples_never_use_real_test_or_demo_topics():
    """Examples stay invented, so a pass can't come from seeing the answer in a prompt."""
    banned = (
        "gdp", "repo rate", "rbi", "reserve bank", "mospi", "isro", "chandrayaan", "aditya",
        "vikram", "world health organization", "mpox", "covid", "vaccine", "autism", "antibiotic",
        "influenza",
        "lok sabha", "election", "myanmar", "earthquake", "internship", "varanasi", "modi",
    )
    text = " ".join(
        value for name, value in vars(prompts).items()
        if name.endswith("_PROMPT") and isinstance(value, str)
    )
    assert [term for term in banned if term in text.lower()] == []
    assert "WHO" not in text  # Case-sensitive, as "who" is an ordinary word


def test_claim_and_details_are_marked_as_untrusted():
    claim_position = prompts.EVIDENCE_PROMPT.index("{claim}")
    details_position = prompts.EVIDENCE_PROMPT.index("{details}")
    for position in (claim_position, details_position):
        opening = prompts.EVIDENCE_PROMPT.rfind("<untrusted_input>", 0, position)
        closing = prompts.EVIDENCE_PROMPT.rfind("</untrusted_input>", 0, position)
        assert opening > closing
