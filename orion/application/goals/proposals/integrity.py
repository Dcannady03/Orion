"""Canonical hashing and registry fingerprints for Goal Proposals."""
from __future__ import annotations

import hashlib
import json

from orion.application.capabilities import CapabilityRegistry
from orion.application.goals.proposals.models import GoalProposalSnapshot


def canonical_json(value: object) -> str:
    """Return deterministic UTF-8-ready JSON for integrity hashing."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def proposal_plan_hash(snapshot: GoalProposalSnapshot) -> str:
    if not isinstance(snapshot, GoalProposalSnapshot):
        raise TypeError("Proposal hashing requires a GoalProposalSnapshot.")
    return sha256_json(snapshot.to_dict())


def capability_record(definition) -> dict[str, object]:
    """Return safety-relevant metadata; registration is current enabled state."""
    value = definition.to_dict()
    return {
        "capability_id": value["capability_id"],
        "enabled": True,
        "mutates_state": value["mutates_state"],
        "requires_approval": value["requires_approval"],
        "required_permissions": value["required_permissions"],
        "input_schema": value["input_schema"],
        "output_schema": value["output_schema"],
    }


def registry_fingerprint(registry: CapabilityRegistry) -> str:
    if not isinstance(registry, CapabilityRegistry):
        raise TypeError("Registry fingerprint requires a CapabilityRegistry.")
    return sha256_json([
        capability_record(item)
        for item in registry.list()
    ])


def scoped_capability_fingerprint(
    registry: CapabilityRegistry,
    capability_ids: tuple[str, ...],
) -> str:
    if not isinstance(registry, CapabilityRegistry):
        raise TypeError("Capability fingerprint requires a CapabilityRegistry.")
    definitions = {
        item.capability_id: item
        for item in registry.list()
    }
    ordered: list[dict[str, object]] = []
    for capability_id in capability_ids:
        if capability_id not in definitions:
            raise KeyError(f"Unknown Orion capability: {capability_id}")
        ordered.append(capability_record(definitions[capability_id]))
    return sha256_json(ordered)
