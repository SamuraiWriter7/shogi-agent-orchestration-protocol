#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import Counter, deque
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"
PASS_DIR = ROOT / "examples" / "pass"
FAIL_DIR = ROOT / "examples" / "fail"


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
    counts = Counter(values)
    for value, count in counts.items():
        if count > 1:
            errors.append(f"{label}: duplicate value '{value}'")
    return errors


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
        errors.append("king: max_hops_per_move must be 0 in v0.1")

    if piece["piece_type"] != "king" and "rewrite_human_axis" not in denied:
        errors.append("non-king piece: denied_actions must include 'rewrite_human_axis'")

    return errors


def validate_policy_semantics(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    initial_ids = [rule["rule_id"] for rule in policy["initial_placement_rules"]]
    movement_ids = [rule["rule_id"] for rule in policy["movement_rules"]]
    errors.extend(ensure_unique(initial_ids, "initial_placement_rules.rule_id"))
    errors.extend(ensure_unique(movement_ids, "movement_rules.rule_id"))

    duplicated_across_groups = sorted(set(initial_ids) & set(movement_ids))
    for rule_id in duplicated_across_groups:
        errors.append(f"policy: rule_id '{rule_id}' is reused across rule groups")

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
            errors.append(
                f"node '{node['node_id']}': unknown zone_id '{node['zone_id']}'"
            )
        for adjacent_id in node["adjacent_node_ids"]:
            if adjacent_id not in nodes:
                errors.append(
                    f"node '{node['node_id']}': unknown adjacent node '{adjacent_id}'"
                )
            elif node["node_id"] not in nodes[adjacent_id]["adjacent_node_ids"]:
                errors.append(
                    f"node adjacency must be symmetric: '{node['node_id']}' -> "
                    f"'{adjacent_id}' is not reciprocated"
                )

    occupancy_piece_ids = [item["piece_profile_id"] for item in board["occupancies"]]
    reserve_piece_ids = [item["piece_profile_id"] for item in board["reserve_pool"]]
    errors.extend(ensure_unique(occupancy_piece_ids, "occupancies.piece_profile_id"))
    errors.extend(ensure_unique(reserve_piece_ids, "reserve_pool.piece_profile_id"))

    overlap = sorted(set(occupancy_piece_ids) & set(reserve_piece_ids))
    for piece_id in overlap:
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
            errors.append(
                f"occupancy '{piece_id}': agent_id does not match piece profile"
            )

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
            errors.append(
                f"occupancy '{piece_id}': inactive piece cannot have active placement"
            )

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

    for reserve_entry in board["reserve_pool"]:
        piece_id = reserve_entry["piece_profile_id"]
        if piece_id not in pieces:
            errors.append(f"reserve_pool: unknown piece_profile_id '{piece_id}'")
            continue
        if reserve_entry["agent_id"] != pieces[piece_id]["agent_id"]:
            errors.append(
                f"reserve entry '{piece_id}': agent_id does not match piece profile"
            )

    if board["status"] in {"initializing", "active", "paused"}:
        if active_kings_in_human_control != 1:
            errors.append(
                "board: exactly one active king must occupy a human-control zone"
            )

    king_bindings = {
        piece["human_axis_binding_ref"]
        for piece in pieces.values()
        if piece["piece_type"] == "king"
    }
    if board["human_axis_ref"] not in king_bindings:
        errors.append(
            "board: human_axis_ref is not bound by any registered king profile"
        )

    return errors


def shortest_hops(nodes: dict[str, dict[str, Any]], start: str, target: str) -> int | None:
    if start == target:
        return 0

    queue: deque[tuple[str, int]] = deque([(start, 0)])
    visited = {start}

    while queue:
        current, distance = queue.popleft()
        for neighbor in nodes[current]["adjacent_node_ids"]:
            if neighbor == target:
                return distance + 1
            if neighbor not in visited and neighbor in nodes:
                visited.add(neighbor)
                queue.append((neighbor, distance + 1))

    return None


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
) -> list[str]:
    errors: list[str] = []
    zones = {zone["zone_id"]: zone for zone in board["zones"]}
    nodes = {node["node_id"]: node for node in board["nodes"]}
    occupancies = {
        item["piece_profile_id"]: item for item in board["occupancies"]
    }

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

    if target_zone["security_level"] == "restricted":
        if not piece["movement_profile"]["may_enter_restricted_zones"]:
            errors.append("placement: piece may not enter restricted zones")

    if placement["source_location"] in {"external", "reserve"}:
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
    hops = shortest_hops(nodes, source_node_id, target_node_id)
    if hops is None:
        errors.append("placement: target node is unreachable from source node")
        return errors

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

    return errors


def report_errors(prefix: str, errors: list[str]) -> None:
    for error in errors:
        print(f"  - {prefix}: {error}")


def main() -> int:
    print("=== Shogi Agent Orchestration Protocol v0.1 Validation ===")
    print()

    schemas = {
        "piece": load_schema("shogi-agent-piece-profile.schema.json"),
        "board": load_schema("agent-board-state.schema.json"),
        "placement": load_schema("agent-placement-record.schema.json"),
        "policy": load_schema("legal-move-policy.schema.json"),
    }

    failures: list[str] = []

    piece_documents: dict[str, dict[str, Any]] = {}
    for path in sorted(PASS_DIR.glob("piece-profile.*.example.yaml")):
        print(f"[validate-pass] {path.relative_to(ROOT)}")
        document = load_document(path)
        errors = schema_errors(document, schemas["piece"])
        if errors:
            print("[schema-error]")
            report_errors(str(path.relative_to(ROOT)), errors)
            failures.append(str(path))
            continue
        print("[schema-ok]")

        semantic = validate_piece_semantics(document)
        if semantic:
            print("[semantic-error]")
            report_errors(str(path.relative_to(ROOT)), semantic)
            failures.append(str(path))
            continue
        print("[semantic-ok]")
        piece_documents[document["piece_profile_id"]] = document
        print()

    policy_path = PASS_DIR / "legal-move-policy.example.yaml"
    policy = load_document(policy_path)
    print(f"[validate-pass] {policy_path.relative_to(ROOT)}")
    policy_schema_errors = schema_errors(policy, schemas["policy"])
    if policy_schema_errors:
        print("[schema-error]")
        report_errors(str(policy_path.relative_to(ROOT)), policy_schema_errors)
        failures.append(str(policy_path))
    else:
        print("[schema-ok]")
        semantic = validate_policy_semantics(policy)
        if semantic:
            print("[semantic-error]")
            report_errors(str(policy_path.relative_to(ROOT)), semantic)
            failures.append(str(policy_path))
        else:
            print("[semantic-ok]")
    print()

    board_path = PASS_DIR / "board-state.example.yaml"
    board = load_document(board_path)
    print(f"[validate-pass] {board_path.relative_to(ROOT)}")
    board_schema_errors = schema_errors(board, schemas["board"])
    if board_schema_errors:
        print("[schema-error]")
        report_errors(str(board_path.relative_to(ROOT)), board_schema_errors)
        failures.append(str(board_path))
    else:
        print("[schema-ok]")
        semantic = validate_board_semantics(board, piece_documents)
        if semantic:
            print("[semantic-error]")
            report_errors(str(board_path.relative_to(ROOT)), semantic)
            failures.append(str(board_path))
        else:
            print("[semantic-ok]")
    print()

    placement_path = PASS_DIR / "placement-record.example.yaml"
    placement = load_document(placement_path)
    print(f"[validate-pass] {placement_path.relative_to(ROOT)}")
    placement_schema_errors = schema_errors(placement, schemas["placement"])
    if placement_schema_errors:
        print("[schema-error]")
        report_errors(str(placement_path.relative_to(ROOT)), placement_schema_errors)
        failures.append(str(placement_path))
    else:
        print("[schema-ok]")
        semantic = validate_placement_semantics(
            placement, piece_documents, board, policy
        )
        if semantic:
            print("[semantic-error]")
            report_errors(str(placement_path.relative_to(ROOT)), semantic)
            failures.append(str(placement_path))
        else:
            print("[semantic-ok]")
    print()

    fail_cases = [
        (
            FAIL_DIR / "piece-profile.invalid-piece-type.example.yaml",
            "piece",
            "schema",
        ),
        (
            FAIL_DIR / "board-state.duplicate-occupancy.example.yaml",
            "board",
            "semantic",
        ),
        (
            FAIL_DIR / "placement-record.unknown-piece.example.yaml",
            "placement",
            "semantic",
        ),
        (
            FAIL_DIR / "placement-record.illegal-transition.example.yaml",
            "placement",
            "semantic",
        ),
    ]

    for path, schema_key, expected_stage in fail_cases:
        print(f"[validate-fail] {path.relative_to(ROOT)}")
        document = load_document(path)
        errors = schema_errors(document, schemas[schema_key])

        if expected_stage == "schema":
            if errors:
                print("[expected-schema-error]")
                report_errors(str(path.relative_to(ROOT)), errors)
            else:
                print("[unexpected-pass] expected a schema error")
                failures.append(str(path))
            print()
            continue

        if errors:
            print("[unexpected-schema-error]")
            report_errors(str(path.relative_to(ROOT)), errors)
            failures.append(str(path))
            print()
            continue

        print("[schema-ok]")
        if schema_key == "board":
            semantic = validate_board_semantics(document, piece_documents)
        elif schema_key == "placement":
            semantic = validate_placement_semantics(
                document, piece_documents, board, policy
            )
        else:
            semantic = []

        if semantic:
            print("[expected-semantic-error]")
            report_errors(str(path.relative_to(ROOT)), semantic)
        else:
            print("[unexpected-pass] expected a semantic error")
            failures.append(str(path))
        print()

    if failures:
        print("Validation failed.")
        return 1

    print("All schemas, pass examples, and expected-fail examples validated successfully.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, yaml.YAMLError, json.JSONDecodeError, ValidationFailure) as exc:
        print(f"[fatal] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
