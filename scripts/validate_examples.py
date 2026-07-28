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

PROMOTION_CHECK_KEYS = [
    "mission_in_scope",
    "board_reference_current",
    "piece_registered",
    "agent_matches_piece",
    "source_matches_board",
    "piece_active",
    "piece_promotable",
    "promotion_type_allowed",
    "zone_allows_promotion",
    "capabilities_allowed",
    "tools_allowed",
    "data_scopes_allowed",
    "authority_scope_allowed",
    "resource_multiplier_within_limit",
    "duration_within_limit",
    "no_active_promotion",
    "human_axis_protected",
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
        errors.append("king: max_hops_per_move must be 0 in v0.5")

    if piece["piece_type"] != "king" and "rewrite_human_axis" not in denied:
        errors.append("non-king piece: denied_actions must include 'rewrite_human_axis'")

    promotion = piece.get("promotion_profile")
    if promotion:
        if piece["piece_type"] in {"king", "gold"}:
            errors.append(f"{piece['piece_type']}: this piece type cannot define a promotion_profile")
        additions = set(promotion["allowed_capability_additions"])
        forbidden = sorted(additions & denied)
        if forbidden:
            errors.append("promotion_profile: capability additions conflict with denied_actions: " + ", ".join(forbidden))
        already_present = sorted(additions & allowed)
        if already_present:
            errors.append("promotion_profile: capability additions must be new capabilities: " + ", ".join(already_present))
        if "rewrite_human_axis" in additions:
            errors.append("promotion_profile: rewrite_human_axis can never be granted by promotion")

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

        promotion_state = occupancy.get("promotion_state")
        binding_ref = occupancy.get("promotion_binding_ref")
        effective_piece_type = occupancy.get("effective_piece_type")
        if promotion_state == "promoted":
            promotion_profile = piece.get("promotion_profile")
            if promotion_profile is None:
                errors.append(f"occupancy '{piece_id}': promoted state requires a promotable piece profile")
            elif effective_piece_type not in promotion_profile["allowed_promoted_piece_types"]:
                errors.append(f"occupancy '{piece_id}': effective promoted piece type is not allowed by the piece profile")
            if piece["piece_type"] in {"king", "gold"}:
                errors.append(f"occupancy '{piece_id}': king and gold pieces cannot be promoted")
            if not binding_ref:
                errors.append(f"occupancy '{piece_id}': promoted state requires promotion_binding_ref")
        elif promotion_state == "base":
            if binding_ref:
                errors.append(f"occupancy '{piece_id}': base state cannot retain promotion_binding_ref")
            if effective_piece_type and effective_piece_type != piece["piece_type"]:
                errors.append(f"occupancy '{piece_id}': base state effective_piece_type must match the piece profile")
        elif binding_ref:
            errors.append(f"occupancy '{piece_id}': promotion_binding_ref requires promotion_state 'promoted'")

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

def resource_budget_within_multiplier(
    requested: dict[str, Any],
    base: dict[str, Any],
    multiplier: float,
) -> bool:
    if requested["currency"] != base["currency"]:
        return False
    numeric_fields = [
        "max_tokens_per_move",
        "max_cost_per_move",
        "max_duration_seconds",
        "max_concurrent_actions",
    ]
    return all(requested[field] <= base[field] * multiplier for field in numeric_fields)


def assess_promotion(
    request: dict[str, Any],
    pieces: dict[str, dict[str, Any]],
    board: dict[str, Any],
) -> dict[str, Any]:
    checks = {key: False for key in PROMOTION_CHECK_KEYS}
    reasons: list[dict[str, str]] = []
    nodes = {node["node_id"]: node for node in board["nodes"]}
    zones = {zone["zone_id"]: zone for zone in board["zones"]}
    occupancies = occupancy_by_piece(board)

    checks["mission_in_scope"] = request["mission_id"] == board["mission_id"]
    checks["board_reference_current"] = (
        request["board_state_ref"] == board["board_state_id"]
        and request["board_revision"] == board["board_revision"]
    )
    piece = pieces.get(request["piece_profile_id"])
    checks["piece_registered"] = piece is not None
    checks["agent_matches_piece"] = bool(piece and request["agent_id"] == piece["agent_id"])
    occupancy = occupancies.get(request["piece_profile_id"])
    checks["source_matches_board"] = bool(
        occupancy and occupancy["node_id"] == request["source_node_id"]
    )
    checks["piece_active"] = bool(
        piece
        and occupancy
        and piece["lifecycle_state"] == "active"
        and occupancy["placement_state"] == "active"
    )
    profile = piece.get("promotion_profile") if piece else None
    checks["piece_promotable"] = bool(
        profile and piece["piece_type"] not in {"king", "gold"}
    )
    checks["promotion_type_allowed"] = bool(
        profile
        and request["requested_promoted_piece_type"]
        in profile["allowed_promoted_piece_types"]
    )
    source_node = nodes.get(request["source_node_id"])
    source_zone = zones.get(source_node["zone_id"]) if source_node else None
    checks["zone_allows_promotion"] = bool(
        profile and source_zone and source_zone["zone_type"] in profile["allowed_zone_types"]
    )
    denied = set(piece["capability_profile"]["denied_actions"]) if piece else set()
    requested_capabilities = set(request["requested_capability_additions"])
    checks["capabilities_allowed"] = bool(
        profile
        and requested_capabilities.issubset(set(profile["allowed_capability_additions"]))
        and not requested_capabilities.intersection(denied)
    )
    checks["tools_allowed"] = bool(
        profile
        and set(request["requested_tool_additions"]).issubset(
            set(profile["allowed_tool_additions"])
        )
    )
    checks["data_scopes_allowed"] = bool(
        profile
        and set(request["requested_data_scope_additions"]).issubset(
            set(profile["allowed_data_scope_additions"])
        )
    )
    checks["authority_scope_allowed"] = bool(
        profile
        and request["requested_authority_scope_ref"]
        in profile["allowed_authority_scope_refs"]
    )
    checks["resource_multiplier_within_limit"] = bool(
        profile
        and piece
        and resource_budget_within_multiplier(
            request["requested_resource_budget"],
            piece["resource_budget"],
            profile["max_resource_multiplier"],
        )
    )
    checks["duration_within_limit"] = bool(
        profile and request["requested_duration_seconds"] <= profile["max_duration_seconds"]
    )
    checks["no_active_promotion"] = bool(
        occupancy
        and occupancy.get("promotion_state", "base") != "promoted"
        and not occupancy.get("promotion_binding_ref")
    )
    checks["human_axis_protected"] = bool(
        piece
        and piece["piece_type"] != "king"
        and "rewrite_human_axis" not in requested_capabilities
    )
    essential = [key for key in PROMOTION_CHECK_KEYS if key != "all_checks_passed"]
    checks["all_checks_passed"] = all(checks[key] for key in essential)

    reason_map = [
        ("mission_in_scope", "MISSION_OUT_OF_SCOPE", "The request mission does not match the board."),
        ("board_reference_current", "STALE_BOARD_REFERENCE", "The request does not reference the current board revision."),
        ("piece_registered", "UNKNOWN_PIECE", "The requested piece profile is not registered."),
        ("agent_matches_piece", "AGENT_PROFILE_MISMATCH", "The agent identifier does not match the piece profile."),
        ("source_matches_board", "SOURCE_OCCUPANCY_MISMATCH", "The requested source node does not match board occupancy."),
        ("piece_active", "PIECE_NOT_ACTIVE", "Only an active board piece can be promoted."),
        ("piece_promotable", "PIECE_NOT_PROMOTABLE", "The piece profile does not permit promotion."),
        ("promotion_type_allowed", "PROMOTION_TYPE_FORBIDDEN", "The requested promoted piece type is not allowed."),
        ("zone_allows_promotion", "PROMOTION_ZONE_FORBIDDEN", "The current board zone does not permit promotion."),
        ("capabilities_allowed", "CAPABILITY_ADDITION_FORBIDDEN", "One or more requested capability additions are not allowed."),
        ("tools_allowed", "TOOL_ADDITION_FORBIDDEN", "One or more requested tool additions are not allowed."),
        ("data_scopes_allowed", "DATA_SCOPE_ADDITION_FORBIDDEN", "One or more requested data-scope additions are not allowed."),
        ("authority_scope_allowed", "AUTHORITY_SCOPE_FORBIDDEN", "The requested authority scope is not allowed."),
        ("resource_multiplier_within_limit", "RESOURCE_MULTIPLIER_EXCEEDED", "The requested budget exceeds the promotion multiplier."),
        ("duration_within_limit", "PROMOTION_DURATION_EXCEEDED", "The requested duration exceeds the piece promotion limit."),
        ("no_active_promotion", "PROMOTION_ALREADY_ACTIVE", "The piece already has an active promotion binding."),
        ("human_axis_protected", "HUMAN_AXIS_PROMOTION_FORBIDDEN", "Promotion cannot modify or replace the human axis."),
    ]
    if not checks["all_checks_passed"]:
        for key, code, message in reason_map:
            if not checks[key]:
                reasons.append({"code": code, "message": message})

    if checks["all_checks_passed"]:
        human_review = bool(profile and profile["requires_human_review"])
        decision = "human-review" if human_review else "eligible"
    else:
        human_review = False
        decision = "ineligible"
    return {
        "decision": decision,
        "checks": checks,
        "required_controls": {
            "authorization_required": True,
            "human_review_required": human_review,
        },
        "assessment_reasons": reasons,
    }


def validate_promotion_request_semantics(
    request: dict[str, Any],
    pieces: dict[str, dict[str, Any]],
    board: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    piece = pieces.get(request["piece_profile_id"])
    if piece and request["base_piece_type"] != piece["piece_type"]:
        errors.append("promotion request: base_piece_type does not match the piece profile")
    if parse_time(request["valid_until"]) <= parse_time(request["requested_at"]):
        errors.append("promotion request: valid_until must be later than requested_at")
    if request["requested_duration_seconds"] > (
        parse_time(request["valid_until"]) - parse_time(request["requested_at"])
    ).total_seconds():
        errors.append("promotion request: requested duration exceeds the request validity window")
    assessment = assess_promotion(request, pieces, board)
    if assessment["decision"] == "ineligible":
        for reason in assessment["assessment_reasons"]:
            errors.append(f"promotion request: {reason['code']}: {reason['message']}")
    return errors


def validate_promotion_assessment_semantics(
    assessment: dict[str, Any],
    requests: dict[str, dict[str, Any]],
    pieces: dict[str, dict[str, Any]],
    board: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    request = requests.get(assessment["promotion_request_ref"])
    if request is None:
        return ["promotion assessment: promotion_request_ref does not resolve"]
    comparisons = {
        "mission_id": "mission_id",
        "board_state_ref": "board_state_ref",
        "evaluated_board_revision": "board_revision",
        "piece_profile_id": "piece_profile_id",
        "agent_id": "agent_id",
    }
    for assessment_field, request_field in comparisons.items():
        if assessment[assessment_field] != request[request_field]:
            errors.append(f"promotion assessment: {assessment_field} does not match the request")
    expected = assess_promotion(request, pieces, board)
    if assessment["decision"] != expected["decision"]:
        errors.append(
            f"promotion assessment: decision '{assessment['decision']}' does not match recomputed decision '{expected['decision']}'"
        )
    for key in PROMOTION_CHECK_KEYS:
        if assessment["checks"][key] != expected["checks"][key]:
            errors.append(f"promotion assessment: check '{key}' does not match recomputed result")
    if assessment["required_controls"] != expected["required_controls"]:
        errors.append("promotion assessment: required_controls do not match recomputed controls")
    actual_codes = [item["code"] for item in assessment["assessment_reasons"]]
    expected_codes = [item["code"] for item in expected["assessment_reasons"]]
    if actual_codes != expected_codes:
        errors.append(
            f"promotion assessment: reason codes do not match recomputed failures (expected {expected_codes}, got {actual_codes})"
        )
    evaluated_at = parse_time(assessment["evaluated_at"])
    if evaluated_at < parse_time(request["requested_at"]):
        errors.append("promotion assessment: evaluated_at cannot precede requested_at")
    if parse_time(assessment["valid_until"]) <= evaluated_at:
        errors.append("promotion assessment: valid_until must be later than evaluated_at")
    if parse_time(assessment["valid_until"]) > parse_time(request["valid_until"]):
        errors.append("promotion assessment: validity cannot exceed the promotion request")
    return errors


def validate_promotion_binding_semantics(
    binding: dict[str, Any],
    requests: dict[str, dict[str, Any]],
    assessments: dict[str, dict[str, Any]],
    boards: dict[str, dict[str, Any]],
    pieces: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    request = requests.get(binding["promotion_request_ref"])
    assessment = assessments.get(binding["promotion_eligibility_assessment_ref"])
    before = boards.get(binding["board_state_before_ref"])
    after = boards.get(binding["board_state_after_ref"])
    piece = pieces.get(binding["piece_profile_id"])
    if request is None:
        errors.append("promotion binding: promotion_request_ref does not resolve")
        return errors
    if assessment is None:
        errors.append("promotion binding: promotion_eligibility_assessment_ref does not resolve")
        return errors
    if assessment["promotion_request_ref"] != request["promotion_request_id"]:
        errors.append("promotion binding: request and assessment lineage do not match")
    if assessment["decision"] not in {"eligible", "human-review"}:
        errors.append("promotion binding: ineligible assessment cannot create a binding")
    for field in ("mission_id", "piece_profile_id", "agent_id", "base_piece_type"):
        if binding[field] != request[field]:
            errors.append(f"promotion binding: {field} does not match the request")
    if binding["promoted_piece_type"] != request["requested_promoted_piece_type"]:
        errors.append("promotion binding: promoted_piece_type does not match the request")
    if binding["source_node_id"] != request["source_node_id"]:
        errors.append("promotion binding: source_node_id does not match the request")
    set_fields = [
        ("granted_capability_additions", "requested_capability_additions"),
        ("granted_tool_additions", "requested_tool_additions"),
        ("granted_data_scope_additions", "requested_data_scope_additions"),
    ]
    for binding_field, request_field in set_fields:
        if set(binding[binding_field]) != set(request[request_field]):
            errors.append(f"promotion binding: {binding_field} does not exactly match the authorized request")
    if binding["authority_scope_binding_ref"] != request["requested_authority_scope_ref"]:
        errors.append("promotion binding: authority scope does not match the authorized request")
    if binding["effective_resource_budget"] != request["requested_resource_budget"]:
        errors.append("promotion binding: effective resource budget does not match the authorized request")
    if piece:
        denied = set(piece["capability_profile"]["denied_actions"])
        conflicts = sorted(set(binding["granted_capability_additions"]) & denied)
        if conflicts:
            errors.append("promotion binding: granted capabilities conflict with denied actions: " + ", ".join(conflicts))
    if not binding.get("authorization_ref"):
        errors.append("promotion binding: authorization_ref is required")
    if assessment["required_controls"]["human_review_required"] and not binding.get("human_review_ref"):
        errors.append("promotion binding: human_review_ref is required")
    if binding["binding_status"] != "active":
        errors.append("promotion binding: canonical applied example must be active")
    if before is None or after is None:
        errors.append("promotion binding: before and after board states must resolve")
    else:
        if binding["board_revision_before"] != before["board_revision"]:
            errors.append("promotion binding: board_revision_before does not match before board")
        if binding["board_revision_after"] != after["board_revision"]:
            errors.append("promotion binding: board_revision_after does not match after board")
        if after["board_revision"] != before["board_revision"] + 1:
            errors.append("promotion binding: board revision must advance by one")
        before_occ = occupancy_by_piece(before).get(binding["piece_profile_id"])
        after_occ = occupancy_by_piece(after).get(binding["piece_profile_id"])
        if before_occ is None or before_occ["node_id"] != binding["source_node_id"]:
            errors.append("promotion binding: piece is not active at source_node_id on before board")
        elif before_occ.get("promotion_state", "base") == "promoted":
            errors.append("promotion binding: piece is already promoted on before board")
        if after_occ is None or after_occ["node_id"] != binding["source_node_id"]:
            errors.append("promotion binding: after board must retain the piece at source_node_id")
        else:
            if after_occ.get("promotion_state") != "promoted":
                errors.append("promotion binding: after board must mark the piece as promoted")
            if after_occ.get("promotion_binding_ref") != binding["promoted_capability_binding_id"]:
                errors.append("promotion binding: after-board promotion_binding_ref does not match")
            if after_occ.get("effective_piece_type") != binding["promoted_piece_type"]:
                errors.append("promotion binding: after-board effective_piece_type does not match")
    effective_at = parse_time(binding["effective_at"])
    expires_at = parse_time(binding["expires_at"])
    if expires_at <= effective_at:
        errors.append("promotion binding: expires_at must be later than effective_at")
    if effective_at < parse_time(assessment["evaluated_at"]):
        errors.append("promotion binding: effective_at cannot precede the eligibility assessment")
    if effective_at > parse_time(assessment["valid_until"]):
        errors.append("promotion binding: assessment expired before the binding became effective")
    if effective_at < parse_time(request["requested_at"]):
        errors.append("promotion binding: effective_at cannot precede the request")
    if expires_at > parse_time(request["valid_until"]):
        errors.append("promotion binding: expires_at cannot exceed request valid_until")
    if (expires_at - effective_at).total_seconds() > request["requested_duration_seconds"]:
        errors.append("promotion binding: effective duration exceeds requested_duration_seconds")
    return errors


def validate_demotion_semantics(
    record: dict[str, Any],
    bindings: dict[str, dict[str, Any]],
    boards: dict[str, dict[str, Any]],
    pieces: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    binding = bindings.get(record["promotion_binding_ref"])
    before = boards.get(record["board_state_before_ref"])
    after = boards.get(record["board_state_after_ref"])
    piece = pieces.get(record["piece_profile_id"])
    if binding is None:
        return ["demotion: promotion_binding_ref does not resolve"]
    for field in ("mission_id", "piece_profile_id", "agent_id"):
        if record[field] != binding[field]:
            errors.append(f"demotion: {field} does not match the promotion binding")
    if record["source_node_id"] != binding["source_node_id"]:
        errors.append("demotion: source_node_id does not match the promotion binding")
    if set(record["revoked_capabilities"]) != set(binding["granted_capability_additions"]):
        errors.append("demotion: revoked_capabilities must exactly remove the promoted additions")
    if set(record["revoked_tools"]) != set(binding["granted_tool_additions"]):
        errors.append("demotion: revoked_tools must exactly remove the promoted additions")
    if set(record["revoked_data_scopes"]) != set(binding["granted_data_scope_additions"]):
        errors.append("demotion: revoked_data_scopes must exactly remove the promoted additions")
    if piece is None:
        errors.append("demotion: piece_profile_id does not resolve")
    else:
        if record["restored_authority_scope_ref"] != piece["authority_scope"]["authority_scope_ref"]:
            errors.append("demotion: restored authority scope does not match the base piece profile")
        if record["restored_resource_budget"] != piece["resource_budget"]:
            errors.append("demotion: restored resource budget does not match the base piece profile")
    if record["demotion_status"] == "applied" and not record.get("authorization_ref"):
        errors.append("demotion: applied demotion requires authorization_ref")
    if before is None or after is None:
        errors.append("demotion: before and after board states must resolve")
    else:
        if record["board_revision_before"] != before["board_revision"]:
            errors.append("demotion: board_revision_before does not match before board")
        if record["board_revision_after"] != after["board_revision"]:
            errors.append("demotion: board_revision_after does not match after board")
        if after["board_revision"] != before["board_revision"] + 1:
            errors.append("demotion: board revision must advance by one")
        before_occ = occupancy_by_piece(before).get(record["piece_profile_id"])
        after_occ = occupancy_by_piece(after).get(record["piece_profile_id"])
        if before_occ is None or before_occ.get("promotion_binding_ref") != binding["promoted_capability_binding_id"]:
            errors.append("demotion: before board does not contain the active promotion binding")
        elif before_occ["node_id"] != record["source_node_id"]:
            errors.append("demotion: before-board source node does not match")
        if after_occ is None or after_occ["node_id"] != record["source_node_id"]:
            errors.append("demotion: after board must retain the piece at source_node_id")
        else:
            if after_occ.get("promotion_state", "base") != "base":
                errors.append("demotion: after board must restore base promotion_state")
            if after_occ.get("promotion_binding_ref"):
                errors.append("demotion: after board must remove promotion_binding_ref")
            if piece and after_occ.get("effective_piece_type", piece["piece_type"]) != piece["piece_type"]:
                errors.append("demotion: after board must restore the base piece type")
    requested_at = parse_time(record["requested_at"])
    applied_at = parse_time(record["applied_at"])
    if applied_at < requested_at:
        errors.append("demotion: applied_at cannot precede requested_at")
    if record["demotion_reason"] == "expiry" and applied_at < parse_time(binding["expires_at"]):
        errors.append("demotion: expiry demotion cannot apply before binding expiry")
    if record["demotion_reason"] != "expiry" and applied_at > parse_time(binding["expires_at"]):
        errors.append("demotion: non-expiry demotion must apply before the binding expires")
    return errors



def validate_mission_profile_semantics(
    profile: dict[str, Any],
    boards: dict[str, dict[str, Any]],
    policy: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    board = boards.get(profile["initial_board_state_ref"])
    if board is None:
        return ["mission profile: initial_board_state_ref does not resolve"]
    if profile["mission_id"] != board["mission_id"]:
        errors.append("mission profile: mission_id does not match the initial board")
    if profile["human_axis_ref"] != board["human_axis_ref"]:
        errors.append("mission profile: human_axis_ref does not match the initial board")
    if board["board_revision"] != 1:
        errors.append("mission profile: the initial board must have revision 1")
    if board["status"] not in {"initializing", "active"}:
        errors.append("mission profile: initial board must be initializing or active")
    if profile["legal_move_policy_ref"] != policy["policy_id"]:
        errors.append("mission profile: legal_move_policy_ref does not match the loaded policy")
    if board["board_policy_ref"] != policy["policy_id"]:
        errors.append("mission profile: initial board policy does not match the loaded policy")
    if profile["mission_id"] not in policy["allowed_mission_ids"]:
        errors.append("mission profile: mission_id is outside the legal move policy scope")
    success_ids = [item["condition_id"] for item in profile["success_conditions"]]
    termination_ids = [item["condition_id"] for item in profile["termination_conditions"]]
    errors.extend(ensure_unique(success_ids, "success_conditions.condition_id"))
    errors.extend(ensure_unique(termination_ids, "termination_conditions.condition_id"))
    for condition_id in sorted(set(success_ids) & set(termination_ids)):
        errors.append(f"mission profile: condition_id '{condition_id}' is reused across condition groups")
    if not any(item["trigger_type"] == "success" and item["outcome"] == "complete" for item in profile["termination_conditions"]):
        errors.append("mission profile: at least one success termination condition must complete the mission")
    required = {"piece-profile", "board-state", "legal-move-policy", "termination-assessment", "lifecycle-audit", "conformance-report"}
    missing = sorted(required - set(profile["required_artifact_types"]))
    if missing:
        errors.append("mission profile: missing required artifact types: " + ", ".join(missing))
    starts_at = parse_time(profile["timebox"]["starts_at"])
    expires_at = parse_time(profile["timebox"]["expires_at"])
    if expires_at <= starts_at:
        errors.append("mission profile: timebox expires_at must be later than starts_at")
    if parse_time(profile["created_at"]) > starts_at:
        errors.append("mission profile: created_at cannot be later than timebox starts_at")
    if profile["max_board_revisions"] < board["board_revision"]:
        errors.append("mission profile: max_board_revisions cannot be below the initial revision")
    return errors


def _condition_result_map(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["condition_id"]: item for item in items}


def validate_termination_assessment_semantics(
    assessment: dict[str, Any],
    profiles: dict[str, dict[str, Any]],
    boards: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    profile = profiles.get(assessment["mission_profile_ref"])
    board = boards.get(assessment["board_state_ref"])
    if profile is None:
        return ["termination assessment: mission_profile_ref does not resolve"]
    if board is None:
        return ["termination assessment: board_state_ref does not resolve"]
    if assessment["mission_id"] != profile["mission_id"] or assessment["mission_id"] != board["mission_id"]:
        errors.append("termination assessment: mission_id lineage is inconsistent")
    if assessment["board_revision"] != board["board_revision"]:
        errors.append("termination assessment: board_revision does not match the referenced board")
    success_expected = {item["condition_id"]: item for item in profile["success_conditions"]}
    termination_expected = {item["condition_id"]: item for item in profile["termination_conditions"]}
    success_actual = _condition_result_map(assessment["evaluated_success_conditions"])
    termination_actual = _condition_result_map(assessment["evaluated_termination_conditions"])
    if set(success_actual) != set(success_expected):
        errors.append("termination assessment: evaluated success condition IDs do not match the mission profile")
    if set(termination_actual) != set(termination_expected):
        errors.append("termination assessment: evaluated termination condition IDs do not match the mission profile")
    for condition_id, result in success_actual.items():
        if result["satisfied"] and not result["evidence_refs"]:
            errors.append(f"termination assessment: satisfied condition '{condition_id}' requires evidence")
    for condition_id, result in termination_actual.items():
        if result["triggered"] and not result["evidence_refs"]:
            errors.append(f"termination assessment: triggered condition '{condition_id}' requires evidence")
    required_success_satisfied = all(
        success_actual.get(condition_id, {}).get("satisfied", False)
        for condition_id, condition in success_expected.items()
        if condition["required"]
    )
    triggered = [termination_expected[cid] for cid, result in termination_actual.items() if result["triggered"] and cid in termination_expected]
    priority = {"terminate": 4, "pause": 3, "human-review": 2, "complete": 1}
    expected_outcome = "continue"
    if triggered:
        selected = max(triggered, key=lambda item: priority[item["outcome"]])
        expected_outcome = selected["outcome"]
    if expected_outcome == "complete" and not required_success_satisfied:
        expected_outcome = "continue"
    board_active_promotions = sorted(
        item.get("promotion_binding_ref") for item in board["occupancies"]
        if item.get("promotion_state") == "promoted" and item.get("promotion_binding_ref")
    )
    board_quarantine = sorted(
        item["reserve_entry_ref"] for item in board["reserve_pool"]
        if item["readiness_state"] == "quarantined"
    )
    controls = assessment["open_controls"]
    if sorted(controls["active_promotion_binding_refs"]) != board_active_promotions:
        errors.append("termination assessment: active promotion controls do not match the board")
    if sorted(controls["quarantined_reserve_entry_refs"]) != board_quarantine:
        errors.append("termination assessment: quarantined reserve controls do not match the board")
    has_open_controls = any(controls[key] for key in controls)
    if assessment["outcome"] in {"complete", "terminate"} and has_open_controls:
        errors.append("termination assessment: a terminal outcome is forbidden while controls remain open")
    if assessment["outcome"] != expected_outcome:
        errors.append(f"termination assessment: outcome '{assessment['outcome']}' does not match recomputed outcome '{expected_outcome}'")
    expected_reason = {"complete":"success", "terminate":"human-stop", "pause":"safety-trigger", "human-review":"timeout", "continue":"conditions-not-met"}[expected_outcome]
    if has_open_controls and expected_outcome == "continue":
        expected_reason = "open-controls"
    if assessment["outcome_reason"] != expected_reason:
        errors.append(f"termination assessment: outcome_reason does not match recomputed reason '{expected_reason}'")
    human_review_required = any(
        condition["requires_human_confirmation"]
        for condition in triggered
    )
    expected_controls = {
        "authorization_required": expected_outcome in {"complete", "terminate"},
        "human_review_required": human_review_required,
    }
    if assessment["required_controls"] != expected_controls:
        errors.append("termination assessment: required_controls do not match recomputed controls")
    if expected_controls["authorization_required"] and not assessment.get("authorization_ref"):
        errors.append("termination assessment: terminal outcome requires authorization_ref")
    if expected_controls["human_review_required"] and not assessment.get("human_review_ref"):
        errors.append("termination assessment: triggered condition requires human_review_ref")
    expected_status = {"complete":"completed", "terminate":"terminated", "pause":"paused"}.get(expected_outcome)
    if expected_status and board["status"] != expected_status:
        errors.append(f"termination assessment: board status must be '{expected_status}' for outcome '{expected_outcome}'")
    if expected_outcome == "continue" and board["status"] not in {"initializing", "active", "paused"}:
        errors.append("termination assessment: continue outcome requires a non-terminal board")
    evaluated_at = parse_time(assessment["evaluated_at"])
    if not (parse_time(profile["timebox"]["starts_at"]) <= evaluated_at <= parse_time(profile["timebox"]["expires_at"])):
        errors.append("termination assessment: evaluated_at is outside the mission timebox")
    if board["board_revision"] > profile["max_board_revisions"]:
        errors.append("termination assessment: board revision exceeds mission maximum")
    return errors


def _refs_resolve(refs: list[str], mapping: dict[str, dict[str, Any]]) -> bool:
    return all(ref in mapping for ref in refs)


def validate_lifecycle_audit_semantics(
    audit: dict[str, Any], profiles: dict[str, dict[str, Any]], boards: dict[str, dict[str, Any]],
    terminations: dict[str, dict[str, Any]], receipts: dict[str, dict[str, Any]], placements: dict[str, dict[str, Any]],
    captures: dict[str, dict[str, Any]], assessments: dict[str, dict[str, Any]], reserves: dict[str, dict[str, Any]],
    redeployments: dict[str, dict[str, Any]], bindings: dict[str, dict[str, Any]], demotions: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    profile = profiles.get(audit["mission_profile_ref"])
    initial = boards.get(audit["initial_board_state_ref"])
    final = boards.get(audit["final_board_state_ref"])
    termination = terminations.get(audit["termination_assessment_ref"])
    if profile is None or initial is None or final is None or termination is None:
        return ["lifecycle audit: mission, board, or termination lineage does not resolve"]
    observed = [boards.get(ref) for ref in audit["observed_board_state_refs"]]
    resolved_boards = [item for item in observed if item is not None]
    revisions = sorted(item["board_revision"] for item in resolved_boards)
    min_rev, max_rev = audit["expected_board_revision_min"], audit["expected_board_revision_max"]
    expected_checks: dict[str, bool] = {}
    expected_checks["board_revisions_contiguous"] = (
        len(resolved_boards) == len(observed) and revisions == list(range(min_rev, max_rev + 1))
        and initial["board_revision"] == min_rev and final["board_revision"] == max_rev
    )
    expected_checks["mission_id_consistent"] = all(item["mission_id"] == audit["mission_id"] for item in resolved_boards) and profile["mission_id"] == audit["mission_id"]
    expected_checks["human_axis_continuous"] = all(item["human_axis_ref"] == profile["human_axis_ref"] for item in resolved_boards)
    expected_checks["legal_policy_consistent"] = all(item["board_policy_ref"] == profile["legal_move_policy_ref"] for item in resolved_boards)
    artifacts = audit["artifact_refs"]
    maps = {
        "move_receipt_refs": receipts, "placement_record_refs": placements, "capture_record_refs": captures,
        "sanitization_assessment_refs": assessments, "reserve_entry_refs": reserves, "redeployment_record_refs": redeployments,
        "promotion_binding_refs": bindings, "demotion_record_refs": demotions,
    }
    expected_checks["all_referenced_artifacts_resolve"] = all(_refs_resolve(artifacts[key], mapping) for key, mapping in maps.items())
    expected_checks["all_executed_moves_authorized"] = all(
        receipt["outcome"] != "executed" or bool(receipt.get("authorization_ref"))
        for ref, receipt in receipts.items() if ref in artifacts["move_receipt_refs"]
    )
    expected_checks["captures_sanitized_before_redeployment"] = all(
        (redeployments[ref]["sanitization_assessment_ref"] in assessments and assessments[redeployments[ref]["sanitization_assessment_ref"]]["decision"] == "passed")
        for ref in artifacts["redeployment_record_refs"] if ref in redeployments
    ) and expected_checks["all_referenced_artifacts_resolve"]
    expected_checks["promotions_reversible"] = all(
        any(record["promotion_binding_ref"] == ref and record["demotion_status"] == "applied" for record in demotions.values())
        for ref in artifacts["promotion_binding_refs"]
    )
    expected_checks["no_active_promotion_at_close"] = not any(item.get("promotion_state") == "promoted" for item in final["occupancies"])
    expected_checks["no_quarantined_reserve_at_close"] = not any(item["readiness_state"] == "quarantined" for item in final["reserve_pool"])
    expected_checks["final_board_terminal"] = final["status"] in {"completed", "terminated"} and termination["board_state_ref"] == final["board_state_id"]
    referenced_docs: list[dict[str, Any]] = []
    for key, mapping in maps.items():
        referenced_docs.extend(mapping[ref] for ref in artifacts[key] if ref in mapping)
    expected_checks["trace_refs_present"] = all(bool(item.get("trace_ref")) for item in referenced_docs)
    expected_checks["all_checks_passed"] = all(expected_checks.values())
    for key, expected in expected_checks.items():
        if audit["checks"][key] != expected:
            errors.append(f"lifecycle audit: check '{key}' does not match recomputed result")
    expected_decision = "conformant" if expected_checks["all_checks_passed"] else "non-conformant"
    if audit["audit_decision"] != expected_decision:
        errors.append(f"lifecycle audit: audit_decision does not match recomputed decision '{expected_decision}'")
    if expected_decision == "conformant" and audit["findings"]:
        errors.append("lifecycle audit: conformant audit must not contain findings")
    if audit["initial_board_state_ref"] != profile["initial_board_state_ref"]:
        errors.append("lifecycle audit: initial board does not match mission profile")
    if termination["mission_profile_ref"] != profile["mission_profile_id"]:
        errors.append("lifecycle audit: termination assessment does not belong to mission profile")
    if parse_time(audit["audited_at"]) < parse_time(termination["evaluated_at"]):
        errors.append("lifecycle audit: audited_at cannot precede termination assessment")
    return errors


def validate_conformance_report_semantics(
    report: dict[str, Any], profiles: dict[str, dict[str, Any]], boards: dict[str, dict[str, Any]],
    terminations: dict[str, dict[str, Any]], audits: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    profile = profiles.get(report["mission_profile_ref"]); board = boards.get(report["final_board_state_ref"])
    termination = terminations.get(report["termination_assessment_ref"]); audit = audits.get(report["lifecycle_audit_ref"])
    if profile is None or board is None or termination is None or audit is None:
        return ["conformance report: one or more lineage references do not resolve"]
    open_controls = termination["open_controls"]
    expected = {
        "schemas_valid": True,
        "semantic_invariants_valid": audit["checks"]["all_checks_passed"],
        "termination_authorized": termination["outcome"] in {"complete", "terminate"} and bool(termination.get("authorization_ref")) and (not termination["required_controls"]["human_review_required"] or bool(termination.get("human_review_ref"))),
        "lifecycle_audit_conformant": audit["audit_decision"] == "conformant",
        "human_axis_preserved": audit["checks"]["human_axis_continuous"],
        "final_board_terminal": board["status"] in {"completed", "terminated"},
        "no_unresolved_controls": not any(open_controls[key] for key in open_controls),
    }
    expected["all_checks_passed"] = all(expected.values())
    for key, value in expected.items():
        if report["checks"][key] != value:
            errors.append(f"conformance report: check '{key}' does not match recomputed result")
    expected_status = "conformant" if expected["all_checks_passed"] else "non-conformant"
    expected_release = "accepted" if expected_status == "conformant" else "rejected"
    if report["conformance_status"] != expected_status:
        errors.append(f"conformance report: conformance_status does not match recomputed status '{expected_status}'")
    if report["release_decision"] != expected_release:
        errors.append(f"conformance report: release_decision does not match recomputed decision '{expected_release}'")
    if report["conformance_profile_id"] != profile["conformance_profile_id"]:
        errors.append("conformance report: conformance_profile_id does not match mission profile")
    if report["mission_id"] != profile["mission_id"] or report["mission_id"] != board["mission_id"]:
        errors.append("conformance report: mission_id lineage is inconsistent")
    if audit["final_board_state_ref"] != board["board_state_id"] or termination["board_state_ref"] != board["board_state_id"]:
        errors.append("conformance report: final board lineage is inconsistent")
    required_evidence = {termination["termination_assessment_id"], audit["lifecycle_audit_id"], board["board_state_id"]}
    if not required_evidence.issubset(set(report["evidence_refs"])):
        errors.append("conformance report: evidence_refs omit required closeout records")
    generated_at = parse_time(report["generated_at"])
    if generated_at < parse_time(audit["audited_at"]) or generated_at < parse_time(termination["evaluated_at"]):
        errors.append("conformance report: generated_at cannot precede its evidence records")
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
    print("=== Shogi Agent Orchestration Protocol v0.5 Validation ===")
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
        "promotion_request": load_schema("agent-promotion-request.schema.json"),
        "promotion_assessment": load_schema("promotion-eligibility-assessment.schema.json"),
        "promotion_binding": load_schema("promoted-capability-binding.schema.json"),
        "demotion": load_schema("agent-demotion-record.schema.json"),
        "mission_profile": load_schema("shogi-agent-mission-profile.schema.json"),
        "termination": load_schema("mission-termination-assessment.schema.json"),
        "lifecycle_audit": load_schema("board-lifecycle-audit-record.schema.json"),
        "conformance": load_schema("shogi-agent-conformance-report.schema.json"),
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

    promotion_requests: dict[str, dict[str, Any]] = {}
    for path in sorted(PASS_DIR.glob("agent-promotion-request.*.example.yaml")):
        raw = load_document(path); board = boards.get(raw["board_state_ref"], base_board)
        document = validate_pass_document(path, schemas["promotion_request"], lambda item, b=board: validate_promotion_request_semantics(item, pieces, b), failures)
        if document:
            promotion_requests[document["promotion_request_id"]] = document

    promotion_assessments: dict[str, dict[str, Any]] = {}
    for path in sorted(PASS_DIR.glob("promotion-eligibility-assessment.*.example.yaml")):
        raw = load_document(path); request = promotion_requests.get(raw["promotion_request_ref"]); board = boards.get(request["board_state_ref"], base_board) if request else base_board
        document = validate_pass_document(path, schemas["promotion_assessment"], lambda item, b=board: validate_promotion_assessment_semantics(item, promotion_requests, pieces, b), failures)
        if document:
            promotion_assessments[document["promotion_eligibility_assessment_id"]] = document

    promotion_bindings: dict[str, dict[str, Any]] = {}
    for path in sorted(PASS_DIR.glob("promoted-capability-binding.*.example.yaml")):
        document = validate_pass_document(path, schemas["promotion_binding"], lambda item: validate_promotion_binding_semantics(item, promotion_requests, promotion_assessments, boards, pieces), failures)
        if document:
            promotion_bindings[document["promoted_capability_binding_id"]] = document

    demotions: dict[str, dict[str, Any]] = {}
    for path in sorted(PASS_DIR.glob("agent-demotion-record.*.example.yaml")):
        document = validate_pass_document(path, schemas["demotion"], lambda item: validate_demotion_semantics(item, promotion_bindings, boards, pieces), failures)
        if document:
            demotions[document["demotion_record_id"]] = document

    mission_profiles: dict[str, dict[str, Any]] = {}
    for path in sorted(PASS_DIR.glob("shogi-agent-mission-profile*.example.yaml")):
        document = validate_pass_document(path, schemas["mission_profile"], lambda item: validate_mission_profile_semantics(item, boards, policy), failures)
        if document:
            mission_profiles[document["mission_profile_id"]] = document

    terminations: dict[str, dict[str, Any]] = {}
    for path in sorted(PASS_DIR.glob("mission-termination-assessment*.example.yaml")):
        document = validate_pass_document(path, schemas["termination"], lambda item: validate_termination_assessment_semantics(item, mission_profiles, boards), failures)
        if document:
            terminations[document["termination_assessment_id"]] = document

    lifecycle_audits: dict[str, dict[str, Any]] = {}
    for path in sorted(PASS_DIR.glob("board-lifecycle-audit-record*.example.yaml")):
        document = validate_pass_document(path, schemas["lifecycle_audit"], lambda item: validate_lifecycle_audit_semantics(item, mission_profiles, boards, terminations, receipts, placements, captures, assessments, reserves, redeployments, promotion_bindings, demotions), failures)
        if document:
            lifecycle_audits[document["lifecycle_audit_id"]] = document

    conformance_reports: dict[str, dict[str, Any]] = {}
    for path in sorted(PASS_DIR.glob("shogi-agent-conformance-report*.example.yaml")):
        document = validate_pass_document(path, schemas["conformance"], lambda item: validate_conformance_report_semantics(item, mission_profiles, boards, terminations, lifecycle_audits), failures)
        if document:
            conformance_reports[document["conformance_report_id"]] = document

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
        (FAIL_DIR / "agent-promotion-request.king-forbidden.example.yaml", "promotion_request", "semantic"),
        (FAIL_DIR / "promotion-eligibility-assessment.false-eligible.example.yaml", "promotion_assessment", "semantic"),
        (FAIL_DIR / "promoted-capability-binding.overextended.example.yaml", "promotion_binding", "semantic"),
        (FAIL_DIR / "agent-demotion-record.binding-mismatch.example.yaml", "demotion", "semantic"),
        (FAIL_DIR / "shogi-agent-mission-profile.policy-mismatch.example.yaml", "mission_profile", "semantic"),
        (FAIL_DIR / "mission-termination-assessment.active-promotion.example.yaml", "termination", "semantic"),
        (FAIL_DIR / "board-lifecycle-audit-record.gapped-revision.example.yaml", "lifecycle_audit", "semantic"),
        (FAIL_DIR / "shogi-agent-conformance-report.false-conformant.example.yaml", "conformance", "semantic"),
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
        elif schema_key == "promotion_request":
            board = boards.get(document["board_state_ref"], base_board)
            semantic = validate_promotion_request_semantics(document, pieces, board)
        elif schema_key == "promotion_assessment":
            request = promotion_requests.get(document["promotion_request_ref"])
            if request is None and document["promotion_request_ref"] == "promotion-request.wind-mission.king-forbidden":
                request = load_document(FAIL_DIR / "agent-promotion-request.king-forbidden.example.yaml")
                request_map = {request["promotion_request_id"]: request}
            else:
                request_map = promotion_requests
            board = boards.get(request["board_state_ref"], base_board) if request else base_board
            semantic = validate_promotion_assessment_semantics(document, request_map, pieces, board)
        elif schema_key == "promotion_binding":
            semantic = validate_promotion_binding_semantics(document, promotion_requests, promotion_assessments, boards, pieces)
        elif schema_key == "demotion":
            semantic = validate_demotion_semantics(document, promotion_bindings, boards, pieces)
        elif schema_key == "mission_profile":
            semantic = validate_mission_profile_semantics(document, boards, policy)
        elif schema_key == "termination":
            semantic = validate_termination_assessment_semantics(document, mission_profiles, boards)
        elif schema_key == "lifecycle_audit":
            semantic = validate_lifecycle_audit_semantics(document, mission_profiles, boards, terminations, receipts, placements, captures, assessments, reserves, redeployments, promotion_bindings, demotions)
        elif schema_key == "conformance":
            semantic = validate_conformance_report_semantics(document, mission_profiles, boards, terminations, lifecycle_audits)
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
