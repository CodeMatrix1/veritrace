from prompts import EVIDENCE_PROMPT, EVIDENCE_PROMPT_VERSION
from schemas.claim import AtomicClaim
from schemas.document import Document, Passage
from schemas.llm_outputs import EvidenceOutput
from services.llm import LLMService


def render_sources(passages: list[Passage], documents: dict[str, Document]) -> str:
    blocks = []
    for passage in passages:
        document = documents[passage.document_id]
        text_by_id = {s.id: s.text for s in document.sentences}
        lines = "\n".join(f"[{sid}] {text_by_id[sid]}" for sid in passage.sentence_ids)
        blocks.append(f'<untrusted_source id="{document.ref}">\n{lines}\n</untrusted_source>')
    return "\n\n".join(blocks)


async def analyze_evidence(
    claim: AtomicClaim,
    passages: list[Passage],
    documents: dict[str, Document],
    llm: LLMService,
    audit_id: str,
) -> EvidenceOutput:
    details = "\n".join(f"{d.id}: {d.text}" for d in claim.key_details) or "(none)"
    prompt = EVIDENCE_PROMPT.format(
        claim=claim.text, details=details, sources=render_sources(passages, documents)
    )
    return await llm.generate_structured(
        prompt, EvidenceOutput, "evidence_analysis", audit_id, EVIDENCE_PROMPT_VERSION
    )
