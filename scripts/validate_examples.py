#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import Counter, deque
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"
PASS_DIR = ROOT / "examples" / "pass"
FAIL_DIR = ROOT / "examples" / "fail"

CHECK_KEYS = [
    "mission_in_scope",
    "board_reference_current",
    "piece_registered",
    "agent_matches_piece",
    "source_matches_board",
    "target_available",
    "target_zone_allows_piece",
    "route_reachable",
    "within_piece_hop_limit",
    "movement_rule_matched",
    "required_capabilities_present",
    "security_boundary_allowed",
    "restricted_zone_entry_allowed",
    "resource_budget_satisfied",
    "all_checks_passed",
]


class ValidationFailure(Exception):
    """Raised when repository validation does not match expectations."""


def load_document(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        if path.suffix == ".json":
            data = json.load(handle)
        else:
            data = yaml.safe_load(handle)

    if not isinstance(data, dict):
        raise ValidationFailure(f"{path}: document root must be an object")
    return data


def load_schema(filename: str) -> dict[str, Any]:
    return load_document(SCHEMA_DIR / filename)


def schema_errors(document: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors: list[str] = []
    for error in sorted(validator.iter_errors(document), key=lambda item: list(item.path)):
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"{location}: {error.message}")
    return errors


def ensure_unique(values: list[str], label: str) -> list[str]:
    errors: list[str] = []
    for value, count in Counter(values).items():
        if count > 1:
            errors.append(f"{label}: duplicate value '{value}'")
    return errors


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_piece_semantics(piece: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    allowed = set(piece["capability_profile"]["allowed_actions"])
    denied = set(piece["capability_profile"]["denied_actions"])
    overlap = sorted(allowed & denied)
    if overlap:
        errors.append(
            "capability_profile: actions cannot be both allowed and denied: "
            + ", ".join(overlap)
        )

    if piece["piece_type"] == "king" and piece["movement_profile"]["max_hops_per_move"] != 0:
        errors.append("king: max_hops_per_move must be 0 in v0.3")

    if piece["piece_type"] != "king" and "rewrite_human_axis" not in denied:
        errors.append("non-king piece: denied_actions must include 'rewrite_human_axis'")

    return errors


def validate_policy_semantics(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    initial_ids = [rule["rule_id"] for rule in policy["initial_placement_rules"]]
    movement_ids = [rule["rule_id"] for rule in policy["movement_rules"]]
    errors.extend(ensure_unique(initial_ids, "initial_placement_rules.rule_id"))
    errors.extend(ensure_unique(movement_ids, "movement_rules.rule_id"))

    for rule_id in sorted(set(initial_ids) & set(movement_ids)):
        errors.append(f"policy: rule_id '{rule_id}' is reused across rule groups")

    signatures: dict[tuple[str, str, str], str] = {}
    for rule in policy["movement_rules"]:
        for piece_type in rule["piece_types"]:
            for from_zone in rule["from_zone_types"]:
                for to_zone in rule["to_zone_types"]:
                    signature = (piece_type, from_zone, to_zone)
                    previous = signatures.get(signature)
                    if previous:
                        errors.append(
                            "movement_rules: ambiguous signature "
                            f"{piece_type} {from_zone} -> {to_zone} is defined by "
                            f"'{previous}' and '{rule['rule_id']}'"
                        )
                    else:
                        signatures[signature] = rule["rule_id"]

    return errors


def validate_board_semantics(
    board: dict[str, Any],
    pieces: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    zone_ids = [zone["zone_id"] for zone in board["zones"]]
    node_ids = [node["node_id"] for node in board["nodes"]]
    errors.extend(ensure_unique(zone_ids, "zones.zone_id"))
    errors.extend(ensure_unique(node_ids, "nodes.node_id"))

    zones = {zone["zone_id"]: zone for zone in board["zones"]}
    nodes = {node["node_id"]: node for node in board["nodes"]}

    for node in board["nodes"]:
        if node["zone_id"] not in zones:
            errors.append(f"node '{node['node_id']}': unknown zone_id '{node['zone_id']}'")
        for adjacent_id in node["adjacent_node_ids"]:
            if adjacent_id not in nodes:
                errors.append(f"node '{node['node_id']}': unknown adjacent node '{adjacent_id}'")
            elif node["node_id"] not in nodes[adjacent_id]["adjacent_node_ids"]:
                errors.append(
                    f"node adjacency must be symmetric: '{node['node_id']}' -> "
                    f"'{adjacent_id}' is not reciprocated"
                )

    occupancy_piece_ids = [item["piece_profile_id"] for item in board["occupancies"]]
    reserve_piece_ids = [item["piece_profile_id"] for item in board["reserve_pool"]]
    errors.extend(ensure_unique(occupancy_piece_ids, "occupancies.piece_profile_id"))
    errors.extend(ensure_unique(reserve_piece_ids, "reserve_pool.piece_profile_id"))

    for piece_id in sorted(set(occupancy_piece_ids) & set(reserve_piece_ids)):
        errors.append(f"piece '{piece_id}' cannot be both on the board and in reserve")

    occupancy_counts: Counter[str] = Counter()
    active_kings_in_human_control = 0

    for occupancy in board["occupancies"]:
        piece_id = occupancy["piece_profile_id"]
        node_id = occupancy["node_id"]
        occupancy_counts[node_id] += 1

        if piece_id not in pieces:
            errors.append(f"occupancy: unknown piece_profile_id '{piece_id}'")
            continue

        piece = pieces[piece_id]
        if occupancy["agent_id"] != piece["agent_id"]:
            errors.append(f"occupancy '{piece_id}': agent_id does not match piece profile")

        if node_id not in nodes:
            errors.append(f"occupancy '{piece_id}': unknown node_id '{node_id}'")
            continue

        node = nodes[node_id]
        if not node["enabled"]:
            errors.append(f"occupancy '{piece_id}': target node '{node_id}' is disabled")

        zone = zones.get(node["zone_id"])
        if zone and piece["piece_type"] not in zone["allowed_piece_types"]:
            errors.append(
                f"occupancy '{piece_id}': piece_type '{piece['piece_type']}' is not "
                f"allowed in zone '{zone['zone_id']}'"
            )

        if piece["lifecycle_state"] != "active" and occupancy["placement_state"] == "active":
            errors.append(f"occupancy '{piece_id}': inactive piece cannot have active placement")

        if (
            piece["piece_type"] == "king"
            and occupancy["placement_state"] == "active"
            and zone
            and zone["zone_type"] == "human-control"
        ):
            active_kings_in_human_control += 1

    for node_id, count in occupancy_counts.items():
        if node_id in nodes and count > nodes[node_id]["capacity"]:
            errors.append(
                f"node '{node_id}': occupancy {count} exceeds capacity "
                f"{nodes[node_id]['capacity']}"
            )

    reserve_entry_ids = [item["reserve_entry_ref"] for item in board["reserve_pool"]]
    errors.extend(ensure_unique(reserve_entry_ids, "reserve_pool.reserve_entry_ref"))

    for reserve_entry in board["reserve_pool"]:
        piece_id = reserve_entry["piece_profile_id"]
        if piece_id not in pieces:
            errors.append(f"reserve_pool: unknown piece_profile_id '{piece_id}'")
            continue
        if reserve_entry["agent_id"] != pieces[piece_id]["agent_id"]:
            errors.append(f"reserve entry '{piece_id}': agent_id does not match piece profile")
        if reserve_entry["source_type"] == "captured" and not reserve_entry.get("capture_record_ref"):
            errors.append(f"reserve entry '{piece_id}': captured source requires capture_record_ref")
        if reserve_entry["readiness_state"] == "ready":
            if not reserve_entry.get("sanitization_assessment_ref"):
                errors.append(f"reserve entry '{piece_id}': ready state requires sanitization_assessment_ref")
            if not reserve_entry.get("authorization_ref"):
                errors.append(f"reserve entry '{piece_id}': ready state requires authorization_ref")

    if board["status"] in {"initializing", "active", "paused"} and active_kings_in_human_control != 1:
        errors.append("board: exactly one active king must occupy a human-control zone")

    king_bindings = {
        piece["human_axis_binding_ref"]
        for piece in pieces.values()
        if piece["piece_type"] == "king"
    }
    if board["human_axis_ref"] not in king_bindings:
        errors.append("board: human_axis_ref is not bound by any registered king profile")

    return errors


def find_shortest_route(
    nodes: dict[str, dict[str, Any]], start: str, target: str
) -> list[str] | None:
    if start not in nodes or target not in nodes:
        return None
    if start == target:
        return [start]

    queue: deque[list[str]] = deque([[start]])
    visited = {start}
    while queue:
        path = queue.popleft()
        current = path[-1]
        for neighbor in nodes[current]["adjacent_node_ids"]:
            if neighbor not in nodes or neighbor in visited:
                continue
            next_path = [*path, neighbor]
            if neighbor == target:
                return next_path
            visited.add(neighbor)
            queue.append(next_path)
    return None


def resource_usage_within_budget(usage: dict[str, Any], piece: dict[str, Any]) -> bool:
    budget = piece["resource_budget"]
    return (
        usage["currency"] == budget["currency"]
        and usage["tokens"] <= budget["max_tokens_per_move"]
        and usage["cost"] <= budget["max_cost_per_move"]
        and usage["duration_seconds"] <= budget["max_duration_seconds"]
        and usage["concurrent_actions"] <= budget["max_concurrent_actions"]
    )


def validate_rule_requirements(
    rule: dict[str, Any],
    piece: dict[str, Any],
    placement: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    capabilities = set(piece["capability_profile"]["allowed_actions"])
    missing = sorted(set(rule.get("required_capabilities", [])) - capabilities)
    if missing:
        errors.append("missing required capabilities: " + ", ".join(missing))
    if rule["requires_authorization"] and not placement.get("authorization_ref"):
        errors.append("authorization_ref is required by the matched rule")
    if rule["requires_human_review"] and not placement.get("human_review_ref"):
        errors.append("human_review_ref is required by the matched rule")
    return errors


def validate_placement_semantics(
    placement: dict[str, Any],
    pieces: dict[str, dict[str, Any]],
    board: dict[str, Any],
    policy: dict[str, Any],
    proposals: dict[str, dict[str, Any]] | None = None,
    evaluations: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    errors: list[str] = []
    zones = {zone["zone_id"]: zone for zone in board["zones"]}
    nodes = {node["node_id"]: node for node in board["nodes"]}
    occupancies = {item["piece_profile_id"]: item for item in board["occupancies"]}

    if placement["mission_id"] != board["mission_id"]:
        errors.append("placement: mission_id does not match the board")
    if placement["mission_id"] not in policy["allowed_mission_ids"]:
        errors.append("placement: mission_id is outside policy scope")
    if placement["legal_move_policy_ref"] != policy["policy_id"]:
        errors.append("placement: legal_move_policy_ref does not match loaded policy")

    piece_id = placement["piece_profile_id"]
    if piece_id not in pieces:
        errors.append(f"placement: unknown piece_profile_id '{piece_id}'")
        return errors

    piece = pieces[piece_id]
    if placement["agent_id"] != piece["agent_id"]:
        errors.append("placement: agent_id does not match piece profile")

    target_node_id = placement["target_node_id"]
    if target_node_id not in nodes:
        errors.append(f"placement: unknown target_node_id '{target_node_id}'")
        return errors

    target_node = nodes[target_node_id]
    if not target_node["enabled"]:
        errors.append(f"placement: target node '{target_node_id}' is disabled")

    target_zone = zones[target_node["zone_id"]]
    if piece["piece_type"] not in target_zone["allowed_piece_types"]:
        errors.append(
            f"placement: piece_type '{piece['piece_type']}' is not allowed in "
            f"target zone '{target_zone['zone_id']}'"
        )
    if target_zone["security_level"] == "restricted" and not piece["movement_profile"]["may_enter_restricted_zones"]:
        errors.append("placement: piece may not enter restricted zones")

    if placement["source_location"] in {"external", "reserve"}:
        if placement["source_location"] == "reserve":
            reserve_matches = [
                item for item in board["reserve_pool"]
                if item["piece_profile_id"] == piece_id
            ]
            if not reserve_matches:
                errors.append("placement: reserve source piece is not present in the board reserve pool")
            else:
                reserve_item = reserve_matches[0]
                if reserve_item["readiness_state"] != "ready":
                    errors.append("placement: reserve source must be in ready state")
                if placement.get("reserve_entry_ref") != reserve_item["reserve_entry_ref"]:
                    errors.append("placement: reserve_entry_ref does not match the current board reserve entry")
                if placement["placement_kind"] != "redeployment":
                    errors.append("placement: reserve source requires placement_kind 'redeployment'")
        matching_rules = [
            rule
            for rule in policy["initial_placement_rules"]
            if piece["piece_type"] in rule["piece_types"]
            and target_zone["zone_type"] in rule["allowed_zone_types"]
        ]
        if not matching_rules:
            errors.append("placement: no legal initial placement rule matched")
            return errors
        if all(validate_rule_requirements(rule, piece, placement) for rule in matching_rules):
            details = [
                "; ".join(validate_rule_requirements(rule, piece, placement))
                for rule in matching_rules
            ]
            errors.append("placement: matched initial rules were unsatisfied: " + " | ".join(details))
        return errors

    source_node_id = placement.get("source_node_id")
    if source_node_id not in nodes:
        errors.append(f"placement: unknown source_node_id '{source_node_id}'")
        return errors

    current = occupancies.get(piece_id)
    if current is None:
        errors.append("placement: piece is not currently on the board")
    elif current["node_id"] != source_node_id:
        errors.append(
            f"placement: source_node_id '{source_node_id}' does not match current "
            f"occupancy '{current['node_id']}'"
        )

    source_node = nodes[source_node_id]
    source_zone = zones[source_node["zone_id"]]
    route = find_shortest_route(nodes, source_node_id, target_node_id)
    if route is None:
        errors.append("placement: target node is unreachable from source node")
        return errors
    hops = len(route) - 1

    if hops > piece["movement_profile"]["max_hops_per_move"]:
        errors.append(
            f"placement: hop distance {hops} exceeds piece limit "
            f"{piece['movement_profile']['max_hops_per_move']}"
        )
    if (
        source_zone["security_level"] != target_zone["security_level"]
        and not piece["movement_profile"]["may_cross_security_zones"]
    ):
        errors.append("placement: piece may not cross security-zone boundaries")

    matching_rules = [
        rule
        for rule in policy["movement_rules"]
        if piece["piece_type"] in rule["piece_types"]
        and source_zone["zone_type"] in rule["from_zone_types"]
        and target_zone["zone_type"] in rule["to_zone_types"]
        and hops <= rule["max_hops"]
    ]
    if not matching_rules:
        errors.append(
            "placement: no legal movement rule matched "
            f"{piece['piece_type']} {source_zone['zone_type']} -> {target_zone['zone_type']}"
        )
        return errors

    satisfied = False
    unsatisfied_details: list[str] = []
    for rule in matching_rules:
        rule_errors = validate_rule_requirements(rule, piece, placement)
        if not rule_errors:
            satisfied = True
            break
        unsatisfied_details.append(f"{rule['rule_id']}: " + "; ".join(rule_errors))
    if not satisfied:
        errors.append(
            "placement: matched movement rules were unsatisfied: "
            + " | ".join(unsatisfied_details)
        )

    if proposals is not None and placement.get("move_proposal_ref"):
        proposal = proposals.get(placement["move_proposal_ref"])
        if proposal is None:
            errors.append("placement: move_proposal_ref does not resolve")
        else:
            for field in ("mission_id", "piece_profile_id", "agent_id", "source_node_id", "target_node_id"):
                if placement.get(field) != proposal.get(field):
                    errors.append(f"placement: {field} does not match referenced proposal")

    if evaluations is not None and placement.get("move_evaluation_ref"):
        evaluation = evaluations.get(placement["move_evaluation_ref"])
        if evaluation is None:
            errors.append("placement: move_evaluation_ref does not resolve")
        elif evaluation["decision"] == "deny":
            errors.append("placement: denied evaluation cannot produce an applied placement")

    return errors


def validate_proposal_semantics(
    proposal: dict[str, Any],
    pieces: dict[str, dict[str, Any]],
    board: dict[str, Any],
    policy: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    occupancies = {item["piece_profile_id"]: item for item in board["occupancies"]}
    nodes = {node["node_id"]: node for node in board["nodes"]}

    if proposal["mission_id"] != board["mission_id"]:
        errors.append("proposal: mission_id does not match the board")
    if proposal["board_state_ref"] != board["board_state_id"]:
        errors.append("proposal: board_state_ref does not match the loaded board")
    if proposal["board_revision"] != board["board_revision"]:
        errors.append("proposal: board_revision is stale or does not match the board")
    if proposal["legal_move_policy_ref"] != policy["policy_id"]:
        errors.append("proposal: legal_move_policy_ref does not match the loaded policy")

    piece = pieces.get(proposal["piece_profile_id"])
    if piece is None:
        errors.append("proposal: piece_profile_id is not registered")
        return errors
    if proposal["agent_id"] != piece["agent_id"]:
        errors.append("proposal: agent_id does not match the piece profile")

    occupancy = occupancies.get(proposal["piece_profile_id"])
    if occupancy is None:
        errors.append("proposal: piece is not currently on the board")
    elif occupancy["node_id"] != proposal["source_node_id"]:
        errors.append("proposal: source_node_id does not match current occupancy")

    if proposal["source_node_id"] not in nodes:
        errors.append("proposal: source_node_id does not exist")
    if proposal["target_node_id"] not in nodes:
        errors.append("proposal: target_node_id does not exist")

    allowed_actions = set(piece["capability_profile"]["allowed_actions"])
    if proposal["requested_action"] not in allowed_actions:
        errors.append("proposal: requested_action is outside the piece capability profile")
    missing = sorted(set(proposal["requested_capabilities"]) - allowed_actions)
    if missing:
        errors.append("proposal: requested_capabilities are unavailable: " + ", ".join(missing))

    if not resource_usage_within_budget(proposal["estimated_resource_usage"], piece):
        errors.append("proposal: estimated_resource_usage exceeds the piece budget")

    if proposal["move_kind"] == "hold" and proposal["source_node_id"] != proposal["target_node_id"]:
        errors.append("proposal: hold move must keep source_node_id and target_node_id equal")
    if proposal["move_kind"] == "reposition" and proposal["source_node_id"] == proposal["target_node_id"]:
        errors.append("proposal: reposition move must change nodes")

    if parse_time(proposal["valid_until"]) <= parse_time(proposal["proposed_at"]):
        errors.append("proposal: valid_until must be later than proposed_at")

    return errors


def assess_move(
    proposal: dict[str, Any],
    pieces: dict[str, dict[str, Any]],
    board: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    checks = {key: False for key in CHECK_KEYS}
    reasons: list[dict[str, str]] = []
    zones = {zone["zone_id"]: zone for zone in board["zones"]}
    nodes = {node["node_id"]: node for node in board["nodes"]}
    occupancies = {item["piece_profile_id"]: item for item in board["occupancies"]}

    checks["mission_in_scope"] = (
        proposal["mission_id"] == board["mission_id"]
        and proposal["mission_id"] in policy["allowed_mission_ids"]
    )
    checks["board_reference_current"] = (
        proposal["board_state_ref"] == board["board_state_id"]
        and proposal["board_revision"] == board["board_revision"]
        and proposal["legal_move_policy_ref"] == policy["policy_id"]
    )

    piece = pieces.get(proposal["piece_profile_id"])
    checks["piece_registered"] = piece is not None
    checks["agent_matches_piece"] = bool(piece and proposal["agent_id"] == piece["agent_id"])

    occupancy = occupancies.get(proposal["piece_profile_id"])
    checks["source_matches_board"] = bool(
        occupancy and occupancy["node_id"] == proposal["source_node_id"]
    )

    target_node = nodes.get(proposal["target_node_id"])
    checks["target_available"] = bool(target_node and target_node["enabled"])

    source_node = nodes.get(proposal["source_node_id"])
    source_zone = zones.get(source_node["zone_id"]) if source_node else None
    target_zone = zones.get(target_node["zone_id"]) if target_node else None

    checks["target_zone_allows_piece"] = bool(
        piece and target_zone and piece["piece_type"] in target_zone["allowed_piece_types"]
    )

    route = find_shortest_route(nodes, proposal["source_node_id"], proposal["target_node_id"])
    checks["route_reachable"] = route is not None
    hop_count = len(route) - 1 if route else None
    checks["within_piece_hop_limit"] = bool(
        piece is not None
        and hop_count is not None
        and hop_count <= piece["movement_profile"]["max_hops_per_move"]
    )

    requested_capabilities_ok = bool(
        piece
        and set(proposal["requested_capabilities"]).issubset(
            set(piece["capability_profile"]["allowed_actions"])
        )
        and proposal["requested_action"] in piece["capability_profile"]["allowed_actions"]
    )

    candidate_rules: list[dict[str, Any]] = []
    if piece and source_zone and target_zone and hop_count is not None:
        candidate_rules = [
            rule
            for rule in policy["movement_rules"]
            if piece["piece_type"] in rule["piece_types"]
            and source_zone["zone_type"] in rule["from_zone_types"]
            and target_zone["zone_type"] in rule["to_zone_types"]
            and hop_count <= rule["max_hops"]
        ]
    matched_rule = sorted(candidate_rules, key=lambda rule: rule["rule_id"])[0] if candidate_rules else None
    checks["movement_rule_matched"] = matched_rule is not None

    rule_capabilities_ok = bool(
        piece
        and (
            matched_rule is None
            or set(matched_rule.get("required_capabilities", [])).issubset(
                set(piece["capability_profile"]["allowed_actions"])
            )
        )
    )
    checks["required_capabilities_present"] = requested_capabilities_ok and rule_capabilities_ok

    checks["security_boundary_allowed"] = bool(
        piece
        and source_zone
        and target_zone
        and (
            source_zone["security_level"] == target_zone["security_level"]
            or piece["movement_profile"]["may_cross_security_zones"]
        )
    )
    checks["restricted_zone_entry_allowed"] = bool(
        piece
        and target_zone
        and (
            target_zone["security_level"] != "restricted"
            or piece["movement_profile"]["may_enter_restricted_zones"]
        )
    )
    checks["resource_budget_satisfied"] = bool(
        piece and resource_usage_within_budget(proposal["estimated_resource_usage"], piece)
    )

    essential_keys = [key for key in CHECK_KEYS if key != "all_checks_passed"]
    checks["all_checks_passed"] = all(checks[key] for key in essential_keys)

    reason_map = [
        ("mission_in_scope", "MISSION_OUT_OF_SCOPE", "The mission is outside the board or policy scope."),
        ("board_reference_current", "STALE_BOARD_REFERENCE", "The proposal does not reference the current board revision and policy."),
        ("piece_registered", "UNKNOWN_PIECE", "The piece profile is not registered."),
        ("agent_matches_piece", "AGENT_PROFILE_MISMATCH", "The agent identifier does not match the piece profile."),
        ("source_matches_board", "SOURCE_OCCUPANCY_MISMATCH", "The proposed source does not match current board occupancy."),
        ("target_available", "TARGET_UNAVAILABLE", "The target node is missing or disabled."),
        ("target_zone_allows_piece", "TARGET_ZONE_FORBIDDEN", "The target zone does not allow this piece type."),
        ("route_reachable", "ROUTE_UNREACHABLE", "No route connects the source and target nodes."),
        ("within_piece_hop_limit", "PIECE_HOP_LIMIT_EXCEEDED", "The shortest route exceeds the piece movement limit."),
        ("movement_rule_matched", "NO_LEGAL_MOVEMENT_RULE", "No movement rule permits this piece and zone transition."),
        ("required_capabilities_present", "REQUIRED_CAPABILITY_MISSING", "The piece lacks a requested or rule-required capability."),
        ("security_boundary_allowed", "SECURITY_BOUNDARY_FORBIDDEN", "The piece may not cross the source and target security boundary."),
        ("restricted_zone_entry_allowed", "RESTRICTED_ZONE_ENTRY_FORBIDDEN", "The piece may not enter the restricted target zone."),
        ("resource_budget_satisfied", "RESOURCE_BUDGET_EXCEEDED", "The proposed usage exceeds the piece resource budget."),
    ]
    if not checks["all_checks_passed"]:
        for key, code, message in reason_map:
            if not checks[key]:
                reasons.append({"code": code, "message": message})

    authorization_required = bool(matched_rule and matched_rule["requires_authorization"])
    human_review_required = bool(
        matched_rule
        and (
            matched_rule["requires_human_review"]
            or (target_zone and target_zone["human_review_required"])
        )
    )
    if checks["all_checks_passed"]:
        decision = "human-review" if human_review_required else "allow"
    else:
        decision = "deny"

    return {
        "decision": decision,
        "matched_rule_id": matched_rule["rule_id"] if matched_rule else None,
        "route": {"path_node_ids": route, "hop_count": hop_count} if route is not None else None,
        "checks": checks,
        "required_controls": {
            "authorization_required": authorization_required,
            "human_review_required": human_review_required,
        },
        "evaluation_reasons": reasons,
    }


def validate_evaluation_semantics(
    evaluation: dict[str, Any],
    proposals: dict[str, dict[str, Any]],
    pieces: dict[str, dict[str, Any]],
    board: dict[str, Any],
    policy: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    proposal = proposals.get(evaluation["move_proposal_ref"])
    if proposal is None:
        return ["evaluation: move_proposal_ref does not resolve"]

    comparisons = {
        "mission_id": "mission_id",
        "board_state_ref": "board_state_ref",
        "evaluated_board_revision": "board_revision",
        "piece_profile_id": "piece_profile_id",
        "agent_id": "agent_id",
        "source_node_id": "source_node_id",
        "target_node_id": "target_node_id",
        "legal_move_policy_ref": "legal_move_policy_ref",
    }
    for evaluation_field, proposal_field in comparisons.items():
        if evaluation[evaluation_field] != proposal[proposal_field]:
            errors.append(
                f"evaluation: {evaluation_field} does not match referenced proposal"
            )

    expected = assess_move(proposal, pieces, board, policy)
    if evaluation["decision"] != expected["decision"]:
        errors.append(
            f"evaluation: decision '{evaluation['decision']}' does not match recomputed "
            f"decision '{expected['decision']}'"
        )

    if evaluation.get("matched_rule_id") != expected["matched_rule_id"]:
        errors.append("evaluation: matched_rule_id does not match recomputed rule")
    if evaluation.get("route") != expected["route"]:
        errors.append("evaluation: route does not match the shortest board route")

    for key in CHECK_KEYS:
        if evaluation["checks"][key] != expected["checks"][key]:
            errors.append(f"evaluation: check '{key}' does not match recomputed result")

    if evaluation["required_controls"] != expected["required_controls"]:
        errors.append("evaluation: required_controls do not match the matched rule and target zone")

    actual_codes = [item["code"] for item in evaluation["evaluation_reasons"]]
    expected_codes = [item["code"] for item in expected["evaluation_reasons"]]
    if actual_codes != expected_codes:
        errors.append(
            "evaluation: reason codes do not match recomputed failures "
            f"(expected {expected_codes}, got {actual_codes})"
        )

    proposed_at = parse_time(proposal["proposed_at"])
    proposal_valid_until = parse_time(proposal["valid_until"])
    evaluated_at = parse_time(evaluation["evaluated_at"])
    evaluation_valid_until = parse_time(evaluation["valid_until"])
    if evaluated_at < proposed_at:
        errors.append("evaluation: evaluated_at cannot precede proposed_at")
    if evaluation_valid_until <= evaluated_at:
        errors.append("evaluation: valid_until must be later than evaluated_at")
    if evaluation_valid_until > proposal_valid_until:
        errors.append("evaluation: valid_until cannot exceed proposal valid_until")

    return errors


def validate_receipt_semantics(
    receipt: dict[str, Any],
    proposals: dict[str, dict[str, Any]],
    evaluations: dict[str, dict[str, Any]],
    placements: dict[str, dict[str, Any]],
    pieces: dict[str, dict[str, Any]],
    board: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    proposal = proposals.get(receipt["move_proposal_ref"])
    evaluation = evaluations.get(receipt["move_evaluation_ref"])
    if proposal is None:
        errors.append("receipt: move_proposal_ref does not resolve")
        return errors
    if evaluation is None:
        errors.append("receipt: move_evaluation_ref does not resolve")
        return errors
    if evaluation["move_proposal_ref"] != proposal["move_proposal_id"]:
        errors.append("receipt: proposal and evaluation are not linked to each other")

    for field in ("mission_id", "piece_profile_id", "agent_id", "source_node_id", "target_node_id"):
        if receipt[field] != proposal[field]:
            errors.append(f"receipt: {field} does not match referenced proposal")

    if receipt["evaluation_decision"] != evaluation["decision"]:
        errors.append("receipt: evaluation_decision does not match referenced evaluation")
    if receipt["board_state_before_ref"] != board["board_state_id"]:
        errors.append("receipt: board_state_before_ref does not match loaded board")
    if receipt.get("matched_rule_id") != evaluation.get("matched_rule_id"):
        errors.append("receipt: matched_rule_id does not match referenced evaluation")

    controls = evaluation["required_controls"]
    outcome = receipt["outcome"]
    if evaluation["decision"] == "deny" and outcome != "blocked":
        errors.append("receipt: denied move must be blocked")

    if outcome == "executed":
        if evaluation["decision"] not in {"allow", "human-review"}:
            errors.append("receipt: only allow or human-review decisions may execute")
        if controls["authorization_required"] and not receipt.get("authorization_ref"):
            errors.append("receipt: authorization_ref is required before execution")
        if controls["human_review_required"] and not receipt.get("human_review_ref"):
            errors.append("receipt: human_review_ref is required before execution")
        if receipt.get("failure"):
            errors.append("receipt: executed outcome must not include failure")

        placement = placements.get(receipt.get("placement_record_ref", ""))
        if placement is None:
            errors.append("receipt: placement_record_ref does not resolve")
        else:
            for field in ("mission_id", "piece_profile_id", "agent_id", "source_node_id", "target_node_id"):
                if receipt[field] != placement.get(field):
                    errors.append(f"receipt: {field} does not match placement record")
            if placement["placement_status"] != "applied":
                errors.append("receipt: executed move requires an applied placement record")
            if receipt.get("authorization_ref") != placement.get("authorization_ref"):
                errors.append("receipt: authorization_ref does not match placement record")
            if controls["human_review_required"] and receipt.get("human_review_ref") != placement.get("human_review_ref"):
                errors.append("receipt: human_review_ref does not match placement record")
            if receipt["board_state_after_ref"] != placement["board_state_after_ref"]:
                errors.append("receipt: board_state_after_ref does not match placement record")

        started = parse_time(receipt["execution_started_at"])
        completed = parse_time(receipt["execution_completed_at"])
        recorded = parse_time(receipt["recorded_at"])
        if completed < started:
            errors.append("receipt: execution_completed_at cannot precede execution_started_at")
        if recorded < completed:
            errors.append("receipt: recorded_at cannot precede execution completion")
        if started > parse_time(proposal["valid_until"]):
            errors.append("receipt: execution started after proposal expiry")
        if started > parse_time(evaluation["valid_until"]):
            errors.append("receipt: execution started after evaluation expiry")
    else:
        if receipt["board_update_applied"]:
            errors.append("receipt: non-executed outcome cannot mutate the board")
        if receipt.get("board_state_after_ref"):
            errors.append("receipt: non-executed outcome must not declare board_state_after_ref")
        if receipt.get("placement_record_ref"):
            errors.append("receipt: non-executed outcome must not declare placement_record_ref")

    piece = pieces.get(receipt["piece_profile_id"])
    if piece and not resource_usage_within_budget(receipt["actual_resource_usage"], piece):
        errors.append("receipt: actual_resource_usage exceeds the piece budget")

    return errors



def occupancy_by_piece(board: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["piece_profile_id"]: item for item in board["occupancies"]}


def reserve_by_ref(board: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["reserve_entry_ref"]: item for item in board["reserve_pool"]}


def validate_capture_semantics(
    capture: dict[str, Any],
    pieces: dict[str, dict[str, Any]],
    boards: dict[str, dict[str, Any]],
    receipts: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    before = boards.get(capture["board_state_before_ref"])
    after = boards.get(capture.get("board_state_after_ref", ""))
    receipt = receipts.get(capture["capturing_move_receipt_ref"])
    capturing = pieces.get(capture["capturing_piece_profile_id"])
    captured = pieces.get(capture["captured_piece_profile_id"])

    if before is None:
        return ["capture: board_state_before_ref does not resolve"]
    if capture["mission_id"] != before["mission_id"]:
        errors.append("capture: mission_id does not match the source board")
    if capture["board_revision_before"] != before["board_revision"]:
        errors.append("capture: board_revision_before does not match the source board")
    if capturing is None:
        errors.append("capture: capturing_piece_profile_id does not resolve")
    elif capture["capturing_agent_id"] != capturing["agent_id"]:
        errors.append("capture: capturing_agent_id does not match piece profile")
    if captured is None:
        errors.append("capture: captured_piece_profile_id does not resolve")
    elif capture["captured_agent_id"] != captured["agent_id"]:
        errors.append("capture: captured_agent_id does not match piece profile")
    if capture["capturing_piece_profile_id"] == capture["captured_piece_profile_id"]:
        errors.append("capture: a piece cannot capture itself")
    if captured and captured["piece_type"] == "king":
        errors.append("capture: the human-axis king cannot be captured")

    before_occ = occupancy_by_piece(before)
    capturing_occ = before_occ.get(capture["capturing_piece_profile_id"])
    captured_occ = before_occ.get(capture["captured_piece_profile_id"])
    if capturing_occ is None:
        errors.append("capture: capturing piece is not active on the source board")
    if captured_occ is None:
        errors.append("capture: captured piece is not active on the source board")
    elif captured_occ["node_id"] != capture["capture_node_id"]:
        errors.append("capture: captured piece does not occupy capture_node_id")

    if capturing and capturing_occ:
        effective = capturing_occ.get("effective_side", capturing["side"])
        if capture["receiving_side"] != effective:
            errors.append("capture: receiving_side does not match the capturing piece control side")
    if captured and captured_occ:
        effective = captured_occ.get("effective_side", captured["side"])
        if capture["captured_from_side"] != effective:
            errors.append("capture: captured_from_side does not match board control state")
    if capture["captured_from_side"] == capture["receiving_side"]:
        errors.append("capture: capture requires a change of control side")

    if receipt is None:
        errors.append("capture: capturing_move_receipt_ref does not resolve")
    else:
        if receipt["outcome"] != "executed" or not receipt["board_update_applied"]:
            errors.append("capture: capture requires an executed move receipt")
        if receipt["piece_profile_id"] != capture["capturing_piece_profile_id"]:
            errors.append("capture: move receipt does not belong to the capturing piece")
        if receipt["agent_id"] != capture["capturing_agent_id"]:
            errors.append("capture: move receipt agent does not match capturing agent")
        if receipt["target_node_id"] != capture["capture_node_id"]:
            errors.append("capture: move receipt target does not match capture_node_id")
        if receipt["board_state_before_ref"] != before["board_state_id"]:
            errors.append("capture: move receipt source board does not match")
        if capture.get("board_state_after_ref") != receipt.get("board_state_after_ref"):
            errors.append("capture: board_state_after_ref does not match move receipt")
        if parse_time(capture["recorded_at"]) < parse_time(receipt["recorded_at"]):
            errors.append("capture: recorded_at cannot precede the move receipt")

    if capture["capture_outcome"] == "captured":
        if after is None:
            errors.append("capture: captured outcome requires a resolvable after board")
        else:
            if after["board_revision"] != before["board_revision"] + 1:
                errors.append("capture: after-board revision must increment by one")
            after_occ = occupancy_by_piece(after)
            if capture["captured_piece_profile_id"] in after_occ:
                errors.append("capture: captured piece must be removed from active occupancies")
            moved = after_occ.get(capture["capturing_piece_profile_id"])
            if moved is None or moved["node_id"] != capture["capture_node_id"]:
                errors.append("capture: capturing piece must occupy capture_node_id after capture")
            reserve_matches = [
                item for item in after["reserve_pool"]
                if item["piece_profile_id"] == capture["captured_piece_profile_id"]
            ]
            if not reserve_matches:
                errors.append("capture: captured piece must enter the after-board reserve pool")
            else:
                item = reserve_matches[0]
                if item["readiness_state"] != "quarantined":
                    errors.append("capture: captured piece must enter reserve as quarantined")
                if item.get("capture_record_ref") != capture["capture_record_id"]:
                    errors.append("capture: after-board reserve entry does not reference capture record")
        nodes = {node["node_id"]: node for node in before["nodes"]}
        zones = {zone["zone_id"]: zone for zone in before["zones"]}
        qnode = nodes.get(capture.get("quarantine_node_id", ""))
        if qnode is None or zones.get(qnode["zone_id"], {}).get("zone_type") != "quarantine":
            errors.append("capture: quarantine_node_id must resolve to a quarantine zone")

    return errors


def validate_sanitization_semantics(
    assessment: dict[str, Any],
    captures: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    capture = captures.get(assessment["capture_record_ref"])
    if capture is None:
        return ["sanitization: capture_record_ref does not resolve"]
    for field in ("mission_id", "piece_profile_id", "agent_id"):
        capture_field = {"piece_profile_id": "captured_piece_profile_id", "agent_id": "captured_agent_id"}.get(field, field)
        if assessment[field] != capture[capture_field]:
            errors.append(f"sanitization: {field} does not match capture record")
    if assessment["quarantine_node_id"] != capture.get("quarantine_node_id"):
        errors.append("sanitization: quarantine_node_id does not match capture record")
    checks = assessment["checks"]
    actual_all = all(value for key, value in checks.items() if key != "all_checks_passed")
    if checks["all_checks_passed"] != actual_all:
        errors.append("sanitization: all_checks_passed does not match individual checks")
    if assessment["decision"] == "passed" and not actual_all:
        errors.append("sanitization: passed decision requires every check to pass")
    if assessment["decision"] == "failed" and actual_all:
        errors.append("sanitization: failed decision requires at least one failed check")
    if assessment["decision"] == "human-review" and not assessment.get("human_review_ref"):
        errors.append("sanitization: human-review decision requires human_review_ref")
    required_actions = {"isolate_memory", "revoke_credentials", "detach_tools", "clear_data_scopes"}
    if assessment["decision"] == "passed" and not required_actions.issubset(set(assessment["actions_applied"])):
        errors.append("sanitization: passed decision is missing required sanitization actions")
    started = parse_time(assessment["assessment_started_at"]); completed = parse_time(assessment["assessment_completed_at"]); captured_at = parse_time(capture["recorded_at"])
    if started < captured_at:
        errors.append("sanitization: assessment cannot start before capture")
    if completed < started:
        errors.append("sanitization: assessment_completed_at cannot precede assessment_started_at")
    return errors


def validate_reserve_entry_semantics(
    entry: dict[str, Any],
    pieces: dict[str, dict[str, Any]],
    boards: dict[str, dict[str, Any]],
    captures: dict[str, dict[str, Any]],
    assessments: dict[str, dict[str, Any]],
    previous_entries: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    piece = pieces.get(entry["piece_profile_id"]); board = boards.get(entry["board_state_ref"]); capture = captures.get(entry.get("capture_record_ref", "")); assessment = assessments.get(entry.get("sanitization_assessment_ref", ""))
    if piece is None:
        errors.append("reserve: piece_profile_id does not resolve")
    elif entry["agent_id"] != piece["agent_id"]:
        errors.append("reserve: agent_id does not match piece profile")
    if board is None:
        return errors + ["reserve: board_state_ref does not resolve"]
    if entry["mission_id"] != board["mission_id"] or entry["board_revision"] != board["board_revision"]:
        errors.append("reserve: board mission or revision does not match entry")
    board_item = reserve_by_ref(board).get(entry["reserve_entry_id"])
    if board_item is None:
        errors.append("reserve: board reserve_pool does not contain reserve_entry_id")
    else:
        for field, board_field in (("piece_profile_id","piece_profile_id"),("agent_id","agent_id"),("assigned_side","effective_side"),("source_type","source_type"),("readiness_state","readiness_state")):
            if entry[field] != board_item[board_field]:
                errors.append(f"reserve: {field} does not match board reserve state")
    if entry["piece_profile_id"] in occupancy_by_piece(board):
        errors.append("reserve: piece cannot be active on the board and in reserve")
    if entry["source_type"] == "captured":
        if capture is None:
            errors.append("reserve: captured source requires a valid capture_record_ref")
        else:
            if capture["captured_piece_profile_id"] != entry["piece_profile_id"] or capture["captured_agent_id"] != entry["agent_id"]:
                errors.append("reserve: capture record does not identify this piece")
            if entry["original_side"] != capture["captured_from_side"]:
                errors.append("reserve: original_side does not match capture record")
            if parse_time(entry["entered_at"]) < parse_time(capture["recorded_at"]):
                errors.append("reserve: entered_at cannot precede capture")
    prev_ref = entry.get("previous_reserve_entry_ref")
    if prev_ref:
        prev = previous_entries.get(prev_ref)
        if prev is None:
            errors.append("reserve: previous_reserve_entry_ref does not resolve")
        elif prev["piece_profile_id"] != entry["piece_profile_id"]:
            errors.append("reserve: previous entry belongs to a different piece")
    if entry["readiness_state"] == "quarantined":
        if entry["authority_state"] != "revoked" or entry["memory_state"] != "isolated" or entry["assigned_side"] != "neutral":
            errors.append("reserve: quarantined state requires revoked authority, isolated memory, and neutral side")
    if entry["readiness_state"] == "ready":
        if assessment is None or assessment["decision"] != "passed":
            errors.append("reserve: ready state requires a passed sanitization assessment")
        elif assessment["piece_profile_id"] != entry["piece_profile_id"]:
            errors.append("reserve: sanitization assessment belongs to a different piece")
        if entry["authority_state"] != "re-authorized":
            errors.append("reserve: ready state requires re-authorized authority")
        if entry["memory_state"] != "sanitized":
            errors.append("reserve: ready state requires sanitized memory")
        if not entry.get("authorization_ref"):
            errors.append("reserve: ready state requires authorization_ref")
        if not entry.get("human_review_ref"):
            errors.append("reserve: captured control transfer requires human_review_ref")
        if entry["assigned_side"] == "neutral":
            errors.append("reserve: ready state requires an assigned operational side")
        if not entry.get("ready_at"):
            errors.append("reserve: ready state requires ready_at")
        elif assessment and parse_time(entry["ready_at"]) < parse_time(assessment["assessment_completed_at"]):
            errors.append("reserve: ready_at cannot precede sanitization completion")
    return errors


def validate_redeployment_semantics(
    record: dict[str, Any],
    reserves: dict[str, dict[str, Any]],
    assessments: dict[str, dict[str, Any]],
    placements: dict[str, dict[str, Any]],
    boards: dict[str, dict[str, Any]],
    pieces: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    reserve = reserves.get(record["reserve_entry_ref"]); assessment = assessments.get(record["sanitization_assessment_ref"]); placement = placements.get(record["placement_record_ref"]); before = boards.get(record["board_state_before_ref"]); after = boards.get(record["board_state_after_ref"]); piece = pieces.get(record["piece_profile_id"])
    if reserve is None:
        return ["redeployment: reserve_entry_ref does not resolve"]
    if reserve["readiness_state"] != "ready" or reserve["authority_state"] != "re-authorized" or reserve["memory_state"] != "sanitized":
        errors.append("redeployment: reserve entry is not ready for deployment")
    for field in ("mission_id","piece_profile_id","agent_id","assigned_side"):
        if record[field] != reserve[field]:
            errors.append(f"redeployment: {field} does not match reserve entry")
    if assessment is None or assessment["decision"] != "passed":
        errors.append("redeployment: sanitization_assessment_ref must resolve to a passed assessment")
    elif reserve.get("sanitization_assessment_ref") != assessment["sanitization_assessment_id"]:
        errors.append("redeployment: assessment does not match reserve entry")
    if piece is None or piece["piece_type"] == "king":
        errors.append("redeployment: invalid or protected piece profile")
    if before is None or after is None:
        return errors + ["redeployment: before or after board does not resolve"]
    if record["board_revision_before"] != before["board_revision"] or record["board_revision_after"] != after["board_revision"]:
        errors.append("redeployment: recorded board revisions do not match board states")
    if after["board_revision"] != before["board_revision"] + 1:
        errors.append("redeployment: after-board revision must increment by one")
    if record["reserve_entry_ref"] not in reserve_by_ref(before):
        errors.append("redeployment: reserve entry is absent from before board")
    if record["piece_profile_id"] in occupancy_by_piece(before):
        errors.append("redeployment: piece is already active before redeployment")
    if record["reserve_entry_ref"] in reserve_by_ref(after):
        errors.append("redeployment: applied entry must be removed from after-board reserve")
    occ = occupancy_by_piece(after).get(record["piece_profile_id"])
    if occ is None or occ["node_id"] != record["target_node_id"]:
        errors.append("redeployment: after board does not place the piece at target_node_id")
    elif occ.get("effective_side", piece["side"] if piece else None) != record["assigned_side"]:
        errors.append("redeployment: after-board effective side does not match assigned_side")
    if placement is None:
        errors.append("redeployment: placement_record_ref does not resolve")
    else:
        expected={"mission_id":record["mission_id"],"piece_profile_id":record["piece_profile_id"],"agent_id":record["agent_id"],"target_node_id":record["target_node_id"],"board_state_before_ref":record["board_state_before_ref"],"board_state_after_ref":record["board_state_after_ref"],"reserve_entry_ref":record["reserve_entry_ref"]}
        for field,value in expected.items():
            if placement.get(field)!=value: errors.append(f"redeployment: {field} does not match placement record")
        if placement["source_location"]!="reserve" or placement["placement_kind"]!="redeployment" or placement["placement_status"]!="applied":
            errors.append("redeployment: placement must be an applied reserve redeployment")
        if placement.get("authorization_ref") != record["authorization_ref"]:
            errors.append("redeployment: authorization_ref does not match placement")
        if placement.get("human_review_ref") != record.get("human_review_ref"):
            errors.append("redeployment: human_review_ref does not match placement")
    if reserve["original_side"] != record["assigned_side"] and not record.get("human_review_ref"):
        errors.append("redeployment: control-side change requires human_review_ref")
    if parse_time(record["applied_at"]) < parse_time(record["requested_at"]):
        errors.append("redeployment: applied_at cannot precede requested_at")
    return errors

def report_errors(prefix: str, errors: list[str]) -> None:
    for error in errors:
        print(f"  - {prefix}: {error}")


def validate_pass_document(
    path: Path,
    schema: dict[str, Any],
    semantic_validator: Any,
    failures: list[str],
) -> dict[str, Any] | None:
    print(f"[validate-pass] {path.relative_to(ROOT)}")
    document = load_document(path)
    errors = schema_errors(document, schema)
    if errors:
        print("[schema-error]")
        report_errors(str(path.relative_to(ROOT)), errors)
        failures.append(str(path))
        print()
        return None
    print("[schema-ok]")

    semantic = semantic_validator(document)
    if semantic:
        print("[semantic-error]")
        report_errors(str(path.relative_to(ROOT)), semantic)
        failures.append(str(path))
        print()
        return None
    print("[semantic-ok]")
    print()
    return document


def main() -> int:
    print("=== Shogi Agent Orchestration Protocol v0.3 Validation ===")
    print()

    schemas = {
        "piece": load_schema("shogi-agent-piece-profile.schema.json"),
        "board": load_schema("agent-board-state.schema.json"),
        "placement": load_schema("agent-placement-record.schema.json"),
        "policy": load_schema("legal-move-policy.schema.json"),
        "proposal": load_schema("agent-move-proposal.schema.json"),
        "evaluation": load_schema("legal-move-evaluation.schema.json"),
        "receipt": load_schema("agent-move-receipt.schema.json"),
        "capture": load_schema("agent-capture-record.schema.json"),
        "sanitization": load_schema("agent-sanitization-assessment.schema.json"),
        "reserve": load_schema("reserve-pool-entry.schema.json"),
        "redeployment": load_schema("agent-redeployment-record.schema.json"),
    }
    failures: list[str] = []

    pieces: dict[str, dict[str, Any]] = {}
    for path in sorted(PASS_DIR.glob("piece-profile.*.example.yaml")):
        document = validate_pass_document(path, schemas["piece"], validate_piece_semantics, failures)
        if document:
            pieces[document["piece_profile_id"]] = document

    policy_path = PASS_DIR / "legal-move-policy.example.yaml"
    policy = validate_pass_document(policy_path, schemas["policy"], validate_policy_semantics, failures)
    if policy is None:
        raise ValidationFailure("valid legal move policy is required for semantic checks")

    boards: dict[str, dict[str, Any]] = {}
    for path in sorted(PASS_DIR.glob("board-state*.example.yaml")):
        document = validate_pass_document(path, schemas["board"], lambda item: validate_board_semantics(item, pieces), failures)
        if document:
            boards[document["board_state_id"]] = document
    base_board = boards.get("board-state.wind-mission.0001")
    if base_board is None:
        raise ValidationFailure("base board state is required for semantic checks")

    proposals: dict[str, dict[str, Any]] = {}
    for path in sorted(PASS_DIR.glob("move-proposal.*.example.yaml")):
        raw = load_document(path); board = boards.get(raw["board_state_ref"], base_board)
        document = validate_pass_document(path, schemas["proposal"], lambda item, b=board: validate_proposal_semantics(item, pieces, b, policy), failures)
        if document:
            proposals[document["move_proposal_id"]] = document

    evaluations: dict[str, dict[str, Any]] = {}
    for path in sorted(PASS_DIR.glob("legal-move-evaluation.*.example.yaml")):
        raw = load_document(path); board = boards.get(raw["board_state_ref"], base_board)
        document = validate_pass_document(path, schemas["evaluation"], lambda item, b=board: validate_evaluation_semantics(item, proposals, pieces, b, policy), failures)
        if document:
            evaluations[document["move_evaluation_id"]] = document

    placements: dict[str, dict[str, Any]] = {}
    for path in sorted(PASS_DIR.glob("placement-record*.example.yaml")):
        raw = load_document(path); board = boards.get(raw["board_state_before_ref"], base_board)
        document = validate_pass_document(path, schemas["placement"], lambda item, b=board: validate_placement_semantics(item, pieces, b, policy, proposals, evaluations), failures)
        if document:
            placements[document["placement_record_id"]] = document

    receipts: dict[str, dict[str, Any]] = {}
    for path in sorted(PASS_DIR.glob("agent-move-receipt.*.example.yaml")):
        raw = load_document(path); board = boards.get(raw["board_state_before_ref"], base_board)
        document = validate_pass_document(path, schemas["receipt"], lambda item, b=board: validate_receipt_semantics(item, proposals, evaluations, placements, pieces, b), failures)
        if document:
            receipts[document["move_receipt_id"]] = document

    captures: dict[str, dict[str, Any]] = {}
    for path in sorted(PASS_DIR.glob("agent-capture-record*.example.yaml")):
        document = validate_pass_document(path, schemas["capture"], lambda item: validate_capture_semantics(item, pieces, boards, receipts), failures)
        if document:
            captures[document["capture_record_id"]] = document

    assessments: dict[str, dict[str, Any]] = {}
    for path in sorted(PASS_DIR.glob("agent-sanitization-assessment*.example.yaml")):
        document = validate_pass_document(path, schemas["sanitization"], lambda item: validate_sanitization_semantics(item, captures), failures)
        if document:
            assessments[document["sanitization_assessment_id"]] = document

    reserves: dict[str, dict[str, Any]] = {}
    for path in sorted(PASS_DIR.glob("reserve-pool-entry.*.example.yaml")):
        document = validate_pass_document(path, schemas["reserve"], lambda item: validate_reserve_entry_semantics(item, pieces, boards, captures, assessments, reserves), failures)
        if document:
            reserves[document["reserve_entry_id"]] = document

    redeployments: dict[str, dict[str, Any]] = {}
    for path in sorted(PASS_DIR.glob("agent-redeployment-record*.example.yaml")):
        document = validate_pass_document(path, schemas["redeployment"], lambda item: validate_redeployment_semantics(item, reserves, assessments, placements, boards, pieces), failures)
        if document:
            redeployments[document["redeployment_record_id"]] = document

    fail_cases = [
        (FAIL_DIR / "piece-profile.invalid-piece-type.example.yaml", "piece", "schema"),
        (FAIL_DIR / "board-state.duplicate-occupancy.example.yaml", "board", "semantic"),
        (FAIL_DIR / "placement-record.unknown-piece.example.yaml", "placement", "semantic"),
        (FAIL_DIR / "placement-record.illegal-transition.example.yaml", "placement", "semantic"),
        (FAIL_DIR / "move-proposal.stale-board-revision.example.yaml", "proposal", "semantic"),
        (FAIL_DIR / "legal-move-evaluation.false-allow.example.yaml", "evaluation", "semantic"),
        (FAIL_DIR / "agent-move-receipt.missing-authorization.example.yaml", "receipt", "semantic"),
        (FAIL_DIR / "agent-move-receipt.executed-after-denial.example.yaml", "receipt", "schema"),
        (FAIL_DIR / "agent-capture-record.king-capture.example.yaml", "capture", "semantic"),
        (FAIL_DIR / "agent-sanitization-assessment.false-pass.example.yaml", "sanitization", "semantic"),
        (FAIL_DIR / "reserve-pool-entry.unsafe-ready.example.yaml", "reserve", "semantic"),
        (FAIL_DIR / "agent-redeployment-record.side-mismatch.example.yaml", "redeployment", "semantic"),
    ]

    for path, schema_key, expected_stage in fail_cases:
        print(f"[validate-fail] {path.relative_to(ROOT)}")
        document = load_document(path)
        errors = schema_errors(document, schemas[schema_key])
        if expected_stage == "schema":
            if errors:
                print("[expected-schema-error]"); report_errors(str(path.relative_to(ROOT)), errors)
            else:
                print("[unexpected-pass] expected a schema error"); failures.append(str(path))
            print(); continue
        if errors:
            print("[unexpected-schema-error]"); report_errors(str(path.relative_to(ROOT)), errors); failures.append(str(path)); print(); continue
        print("[schema-ok]")
        if schema_key == "board":
            semantic = validate_board_semantics(document, pieces)
        elif schema_key == "placement":
            board = boards.get(document["board_state_before_ref"], base_board); semantic = validate_placement_semantics(document, pieces, board, policy, proposals, evaluations)
        elif schema_key == "proposal":
            board = boards.get(document["board_state_ref"], base_board); semantic = validate_proposal_semantics(document, pieces, board, policy)
        elif schema_key == "evaluation":
            board = boards.get(document["board_state_ref"], base_board); semantic = validate_evaluation_semantics(document, proposals, pieces, board, policy)
        elif schema_key == "receipt":
            board = boards.get(document["board_state_before_ref"], base_board); semantic = validate_receipt_semantics(document, proposals, evaluations, placements, pieces, board)
        elif schema_key == "capture":
            semantic = validate_capture_semantics(document, pieces, boards, receipts)
        elif schema_key == "sanitization":
            semantic = validate_sanitization_semantics(document, captures)
        elif schema_key == "reserve":
            semantic = validate_reserve_entry_semantics(document, pieces, boards, captures, assessments, reserves)
        elif schema_key == "redeployment":
            semantic = validate_redeployment_semantics(document, reserves, assessments, placements, boards, pieces)
        else:
            semantic = []
        if semantic:
            print("[expected-semantic-error]"); report_errors(str(path.relative_to(ROOT)), semantic)
        else:
            print("[unexpected-pass] expected a semantic error"); failures.append(str(path))
        print()

    if failures:
        print("Validation failed."); return 1
    print("All schemas, pass examples, and expected-fail examples validated successfully.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, yaml.YAMLError, json.JSONDecodeError, ValidationFailure) as exc:
        print(f"[fatal] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
