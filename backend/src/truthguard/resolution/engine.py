"""Deterministic resolution engine (non-negotiable #2: no LLM decides precedence).

Input: the assertions of one slot (already filtered to the requested scope), their
documents, the lineage edges, an as-of date and a precedence policy. Rules run in policy
order; each rule may eliminate assertions, and the chain records what every evaluated rule
did. The first rule after which the surviving evidence is consistent decides.

Consistency: two surviving assertions conflict when their scopes overlap and their
normalized values differ. "all" overlaps every scope; distinct specific scopes do not.

Properties covered by tests (tests/unit/test_resolver_properties.py): deterministic,
independent of input order, and a higher-precedence rule always beats a lower one.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date

from truthguard.domain.enums import ResolutionStatus, RuleId
from truthguard.domain.models import Assertion, Document, LineageEdge, Resolution, RuleEvaluation
from truthguard.ingestion.lineage import section_matches
from truthguard.resolution.policy import PrecedencePolicy

FLAG_UNOFFICIAL = "possible_unofficial_update"
FLAG_INFERRED_SUPERSESSION = "inferred_supersession"
FLAG_INFERRED_EDGE = "inferred_edge"
FLAG_SCOPE_SPLIT = "scope_split"
FLAG_UNDATED = "undated_sources"


def overlap(a: str, b: str) -> bool:
    return a == "all" or b in ("all", a)


@dataclass
class _State:
    alive: dict[str, Assertion]
    docs: dict[str, Document]
    eliminated_by: dict[str, RuleId] = field(default_factory=dict)
    explained_pairs: set[frozenset[str]] = field(default_factory=set)
    flags: set[str] = field(default_factory=set)
    strengths: list[float] = field(default_factory=list)
    escalated: bool = False

    def date_of(self, a: Assertion) -> date | None:
        d = self.docs.get(a.document_id)
        return (d.sort_date if d else None) or a.effective_from

    def tier_of(self, a: Assertion) -> int:
        d = self.docs.get(a.document_id)
        return d.authority_tier if d else 99

    def series_of(self, a: Assertion) -> str:
        d = self.docs.get(a.document_id)
        return d.series_id if d else a.document_id

    def conflicting_pairs(self) -> list[tuple[Assertion, Assertion]]:
        items = sorted(self.alive.values(), key=lambda a: a.id)
        out = []
        for i, a in enumerate(items):
            for b in items[i + 1 :]:
                if (
                    a.value_norm != b.value_norm
                    and overlap(a.scope, b.scope)
                    and frozenset((a.id, b.id)) not in self.explained_pairs
                ):
                    out.append((a, b))
        return out

    def consistent(self) -> bool:
        return not self.conflicting_pairs()

    def eliminate(self, ids: list[str], rule: RuleId) -> list[str]:
        done = []
        for i in sorted(set(ids)):
            if i in self.alive:
                del self.alive[i]
                self.eliminated_by[i] = rule
                done.append(i)
        return done


class Resolver:
    def __init__(self, policy: PrecedencePolicy) -> None:
        self.policy = policy

    def resolve(
        self,
        conflict_id: str,
        slot_key: str,
        assertions: list[Assertion],
        docs: dict[str, Document],
        edges: list[LineageEdge],
        as_of: date,
        requested_scope: str | None = None,
    ) -> Resolution:
        st = _State(alive={a.id: a for a in assertions}, docs=docs)
        chain: list[RuleEvaluation] = []

        quarantined = st.eliminate([a.id for a in assertions if a.quarantined], RuleId.QUARANTINE)
        if quarantined:
            chain.append(
                RuleEvaluation(
                    rule=RuleId.QUARANTINE,
                    applied=True,
                    eliminated=quarantined,
                    note="Excluded assertions from chunks flagged as prompt injection.",
                )
            )
        initially_consistent = st.consistent()
        deciding: RuleId | None = None
        for rule in self.policy.order:
            if st.consistent() and rule not in (RuleId.R0, RuleId.R1):
                break  # nothing left to decide; R0/R1 still run so stale sources never get cited
            handler = getattr(self, f"_{rule.value.lower()}")
            evaluation: RuleEvaluation = handler(st, edges, as_of, requested_scope)
            chain.append(evaluation)
            if evaluation.applied and evaluation.eliminated:
                st.strengths.append(self.policy.strength_of(rule))
            if st.escalated:
                deciding = rule
                break
            if deciding is None and not initially_consistent and st.consistent():
                deciding = rule
            if rule == RuleId.R5:
                break

        winners = self._winners(st)
        if st.escalated or not st.consistent():
            status = ResolutionStatus.UNRESOLVED
            strength = 0.0
            deciding = deciding if st.escalated else RuleId.R5
        elif initially_consistent:
            status = ResolutionStatus.NO_CONFLICT
            strength = 1.0
            deciding = None
        else:
            status = ResolutionStatus.SCOPE_SPLIT if st.explained_pairs else ResolutionStatus.RESOLVED
            strength = min(st.strengths) if st.strengths else 1.0
        if FLAG_INFERRED_EDGE in st.flags and status != ResolutionStatus.UNRESOLVED:
            strength = min(strength, 0.8)
        return Resolution(
            conflict_id=conflict_id,
            slot_key=slot_key,
            status=status,
            winner_assertion_ids=[a.id for a in winners.values()]
            if status != ResolutionStatus.UNRESOLVED
            else [],
            winners_by_scope={s: a.id for s, a in winners.items()}
            if status != ResolutionStatus.UNRESOLVED
            else {},
            deciding_rule=deciding,
            rule_chain=chain,
            eliminated_by=st.eliminated_by,
            rationale=self._rationale(status, deciding, st),
            as_of=as_of,
            strength=round(strength, 3),
            flags=sorted(st.flags),
        )

    # ------------------------------------------------------------------ rules

    def _r0(self, st: _State, edges: list[LineageEdge], as_of: date, scope: str | None) -> RuleEvaluation:
        out = []
        for a in list(st.alive.values()):
            doc = st.docs.get(a.document_id)
            start = (doc.effective_from or doc.published_at) if doc else a.effective_from
            end = doc.effective_to if doc else a.effective_to
            if (start and start > as_of) or (end and end < as_of):
                out.append(a.id)
        gone = st.eliminate(out, RuleId.R0)
        note = (
            f"Excluded {len(gone)} assertion(s) not in force on {as_of.isoformat()}."
            if gone
            else f"All sources are in force on {as_of.isoformat()}."
        )
        return RuleEvaluation(rule=RuleId.R0, applied=bool(gone), eliminated=gone, note=note)

    def _r1(self, st: _State, edges: list[LineageEdge], as_of: date, scope: str | None) -> RuleEvaluation:
        sup = [e for e in edges if e.relation.is_superseding]
        out_edges: dict[str, list[LineageEdge]] = {}
        for e in sup:
            out_edges.setdefault(e.from_doc, []).append(e)
        effective = list(st.alive.values())
        doomed: dict[str, LineageEdge] = {}
        for x in effective:
            for y in effective:
                if y.document_id == x.document_id or not overlap(x.scope, y.scope):
                    continue
                last = self._supersession_path(y.document_id, x, out_edges)
                if last is not None:
                    doomed.setdefault(x.id, last)
        gone = st.eliminate(list(doomed), RuleId.R1)
        for aid in gone:
            e = doomed[aid]
            if e.confidence < 0.8:
                st.flags.add(FLAG_INFERRED_EDGE)
        if gone:
            used = {doomed[a].evidence_span for a in gone}
            note = "Superseded by lineage: " + " / ".join(sorted(used))
        else:
            note = "No lineage edge links the remaining sources for this clause."
        return RuleEvaluation(rule=RuleId.R1, applied=bool(gone), eliminated=gone, note=note)

    @staticmethod
    def _supersession_path(
        src_doc: str, target: Assertion, out_edges: dict[str, list[LineageEdge]]
    ) -> LineageEdge | None:
        """BFS from src_doc over supersedes/amends edges; the hop INTO the target's document
        must cover the clause the target assertion sits in."""
        seen = {src_doc}
        queue = deque([src_doc])
        while queue:
            cur = queue.popleft()
            for e in out_edges.get(cur, []):
                if e.to_doc == target.document_id and section_matches(e.scope_section, target.section_path):
                    return e
                if e.to_doc not in seen and e.to_doc != target.document_id:
                    seen.add(e.to_doc)
                    queue.append(e.to_doc)
        return None

    def _r2(self, st: _State, edges: list[LineageEdge], as_of: date, scope: str | None) -> RuleEvaluation:
        alive = list(st.alive.values())
        if not alive:
            return RuleEvaluation(rule=RuleId.R2, applied=False, note="No sources left.")
        top = min(st.tier_of(a) for a in alive)
        top_items = [a for a in alive if st.tier_of(a) == top]
        out: list[str] = []
        escalations: list[str] = []
        for x in alive:
            if st.tier_of(x) == top:
                continue
            overlapping = [t for t in top_items if overlap(t.scope, x.scope)]
            if not overlapping or x.value_norm in {t.value_norm for t in overlapping}:
                continue  # no comparable official source, or it agrees with one
            x_date = st.date_of(x)
            top_dates = [st.date_of(t) for t in overlapping]
            newer = x_date is not None and all(d is not None and x_date > d for d in top_dates)
            if newer and self.policy.rules.R2.escalate_if_lower_tier_newer:
                escalations.append(x.id)
            else:
                out.append(x.id)
        gone = st.eliminate(out, RuleId.R2)
        if escalations:
            st.escalated = True
            st.flags.add(FLAG_UNOFFICIAL)
            return RuleEvaluation(
                rule=RuleId.R2,
                applied=True,
                eliminated=gone,
                note=(
                    f"A tier-{st.tier_of(st.alive[escalations[0]])} source is newer than the tier-{top} source "
                    "it contradicts: possible unofficial update. Escalated instead of discarding it."
                ),
            )
        note = (
            f"Higher authority (tier {top}) outranks {len(gone)} lower-tier source(s)."
            if gone
            else "No authority difference decides this."
        )
        return RuleEvaluation(rule=RuleId.R2, applied=bool(gone), eliminated=gone, note=note)

    def _r3(self, st: _State, edges: list[LineageEdge], as_of: date, scope: str | None) -> RuleEvaluation:
        pairs = st.conflicting_pairs()

        def specific_outranks_or_ties(a: Assertion, b: Assertion) -> bool:
            general, specific = (a, b) if a.scope == "all" else (b, a)
            # R2 has already removed lower-tier sources that disagree with official ones, so a
            # remaining lower-tier general source here only echoes the official general rule.
            return st.tier_of(specific) <= st.tier_of(general)

        general_vs_specific = [
            (a, b)
            for a, b in pairs
            if (a.scope == "all") != (b.scope == "all") and specific_outranks_or_ties(a, b)
        ]
        if not general_vs_specific:
            return RuleEvaluation(
                rule=RuleId.R3, applied=False, note="No general-vs-specific scope conflict."
            )
        if scope and scope != "all":
            general = [a.id if a.scope == "all" else b.id for a, b in general_vs_specific]
            gone = st.eliminate(general, RuleId.R3)
            return RuleEvaluation(
                rule=RuleId.R3,
                applied=bool(gone),
                eliminated=gone,
                note=f"The rule specific to '{scope}' overrides the general rule for that scope.",
            )
        for a, b in general_vs_specific:
            st.explained_pairs.add(frozenset((a.id, b.id)))
        st.flags.add(FLAG_SCOPE_SPLIT)
        st.strengths.append(self.policy.strength_of(RuleId.R3))
        scopes = sorted({x.scope for p in general_vs_specific for x in p if x.scope != "all"})
        return RuleEvaluation(
            rule=RuleId.R3,
            applied=True,
            note=f"Scope split: the specific rule applies to {', '.join(scopes)}; "
            "the general rule applies everywhere else.",
        )

    def _r4(self, st: _State, edges: list[LineageEdge], as_of: date, scope: str | None) -> RuleEvaluation:
        cross = self.policy.rules.R4.cross_series
        out: set[str] = set()
        for a, b in st.conflicting_pairs():
            if not cross and (st.series_of(a) != st.series_of(b) or st.tier_of(a) != st.tier_of(b)):
                continue
            da, db = st.date_of(a), st.date_of(b)
            if da is None or db is None or da == db:
                if da is None or db is None:
                    st.flags.add(FLAG_UNDATED)
                continue
            out.add(b.id if da > db else a.id)
        gone = st.eliminate(sorted(out), RuleId.R4)
        if gone:
            st.flags.add(FLAG_INFERRED_SUPERSESSION)
            note = (
                "Recency decides (policy: recency beats authority)."
                if cross
                else "Same series and tier with no supersession edge: the newer version wins (inferred supersession)."
            )
        else:
            note = "Recency cannot decide (different series, different tiers, or undated sources)."
        return RuleEvaluation(rule=RuleId.R4, applied=bool(gone), eliminated=gone, note=note)

    def _r5(self, st: _State, edges: list[LineageEdge], as_of: date, scope: str | None) -> RuleEvaluation:
        if st.consistent():
            return RuleEvaluation(rule=RuleId.R5, applied=False, note="Already decided.")
        return RuleEvaluation(
            rule=RuleId.R5,
            applied=True,
            note="No precedence rule decides between the remaining sources: cannot determine.",
        )

    # ------------------------------------------------------------------ helpers

    def _winners(self, st: _State) -> dict[str, Assertion]:
        """One representative per scope: most authoritative, then newest, then id."""
        by_scope: dict[str, list[Assertion]] = {}
        for a in st.alive.values():
            by_scope.setdefault(a.scope, []).append(a)
        out = {}
        for s, items in by_scope.items():
            items.sort(key=lambda a: (st.tier_of(a), -(st.date_of(a) or date.min).toordinal(), a.id))
            out[s] = items[0]
        return out

    @staticmethod
    def _rationale(status: ResolutionStatus, deciding: RuleId | None, st: _State) -> str:
        names = {
            RuleId.R0: "temporal validity",
            RuleId.R1: "explicit supersession",
            RuleId.R2: "authority tier",
            RuleId.R3: "specificity",
            RuleId.R4: "recency in series",
            RuleId.R5: "no decision",
        }
        if status == ResolutionStatus.NO_CONFLICT:
            return "All sources in force agree."
        if status == ResolutionStatus.UNRESOLVED:
            if FLAG_UNOFFICIAL in st.flags:
                return (
                    "A lower-authority source newer than the official one disagrees with it. This may be an "
                    "unofficial update; the corpus does not determine which applies."
                )
            return "Sources of equal standing disagree and no precedence rule decides between them."
        rules = sorted(
            {r for r in st.eliminated_by.values() if r != RuleId.QUARANTINE}, key=lambda r: r.value
        )
        chain = ", ".join(f"{r.value} {names[r]}" for r in rules)
        if status == ResolutionStatus.SCOPE_SPLIT:
            return f"Decided by scope (R3 specificity){'; also ' + chain if chain else ''}."
        return f"Decided by {deciding.value + ' ' + names[deciding] if deciding else 'rules'} (rules applied: {chain})."
