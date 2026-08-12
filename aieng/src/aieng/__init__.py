"""Pure package-semantics library for the .aieng engineering data format.

All modules are pure functions with no I/O — callers supply pre-read data and
receive structured dicts in return. The reference runtime (``aieng-ui``) owns
ZIP I/O, HTTP endpoints, and tool orchestration; this package owns the
semantics that are independent of any specific runtime.

Submodules
----------
- ``aieng.cae_result_summary``  — result summary generation and evidence index
- ``aieng.package_manifest``    — artifact classification and manifest assembly
- ``aieng.evidence_resolver``   — evidence reference freshness resolution
- ``aieng.package_consistency`` — consistency diagnostic checks
- ``aieng.review_readiness``    — claim proposal readiness rollup
- ``aieng.claim_proposal``      — proposal artifact schema and validation
- ``aieng.audit_event``         — audit event schema and JSONL serialisation
- ``aieng.revalidation_status`` — geometry revision state-transition semantics

Import directly from submodules::

    from aieng.audit_event import build_audit_event
    from aieng.claim_proposal import build_claim_proposal, CLAIM_PROPOSAL_STATUSES
    from aieng.revalidation_status import record_geometry_edit_status
"""

__version__ = "0.1.0a3"

FORMAT_VERSION = "0.1.0"
