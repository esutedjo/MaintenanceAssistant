"""Diagnostics: fault-mode signature match and similar past cases from case memory (H-MEM)."""

from agents.runtime import NodeResult, RunContext, node
from casefile.schemas import CaseFile, EvidenceLedger, Findings
from tools.model_tools import ModelTools

SIMILAR_CASES = 3


@node("diagnostics", "match_fault_mode", lambda s: f"asset={s.asset.asset_id if s.asset else None}")
def diagnostics(state: CaseFile, ctx: RunContext) -> NodeResult:
    asset = state.asset
    cycles = ctx.data.cycles(asset.asset_id).frame
    ledger = EvidenceLedger("diagnostics", state.evidence)
    tools = ModelTools(ctx.pack, ledger)
    modes = tools.fault_modes(asset.asset_id, cycles, asset.variant)
    top = modes[0]
    similar = []
    if tools.degradation_detected(cycles):
        mode = ctx.pack["failure_modes"][top.failure_mode]
        hits = ctx.store.search_memory(f"{mode['label']} {mode['component']} removal", top_n=SIMILAR_CASES,
                                       tags=["case", asset.asset_type, top.failure_mode], threshold=0.0)
        for hit in hits:
            evidence = ledger.add("case", f"{ctx.store.backend}:{hit.source}", hit.content[:240],
                                  {"memory_id": hit.id, "content": hit.content, "tags": hit.tags,
                                   "trust": hit.confidence, "relevance": round(hit.relevance, 3)},
                                  degraded=ctx.store.degraded_reason)
            similar.append(evidence.id)
    findings = Findings(fault_modes=modes, similar_cases=similar)
    note = f"; {len(similar)} similar past cases" if similar else "; no degradation, similar-case search skipped"
    return NodeResult({"findings": findings, "evidence": ledger.records},
                      f"{top.failure_mode} p={top.probability:.2f}{note}", confidence=top.probability)
