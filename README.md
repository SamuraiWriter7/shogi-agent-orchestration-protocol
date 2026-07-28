# Shogi Agent Orchestration Protocol

A protocol for role-bounded, position-aware, temporarily promotable, safely redeployable, and auditable multi-agent orchestration inspired by the structural mechanics of shogi.

## Status

* **Current version:** `v0.5.0`
* **Specification maturity:** Experimental
* **Primary format:** JSON Schema with YAML examples
* **Validation:** JSON Schema validation and cross-record semantic checks
* **Core scope:** Mission-bound multi-agent placement, movement, capture, redeployment, promotion, termination, and conformance

> **No piece is universal.
> No move is unlimited.
> No capture transfers trust.
> No promotion is permanent.
> No mission closes with unresolved authority.
> Every transition leaves a trace.**

---

## Overview

The Shogi Agent Orchestration Protocol models a multi-agent system as a governed board:

* an **agent** is represented as a bounded piece;
* an **operational environment** is represented as a board;
* a **role or authority boundary** is represented as a movement rule;
* an **agent action or placement change** is represented as a move;
* an **inactive or captured agent** is represented as a reserve piece;
* a **temporary capability expansion** is represented as promotion;
* a **mission history** is represented as a revisioned board lineage.

The protocol does not attempt to create a single universal agent.

Instead, it defines how specialized agents may be placed, moved, authorized, captured, sanitized, redeployed, temporarily promoted, demoted, audited, and finally released from a mission.

The result is a multi-agent orchestration model in which capability is distributed across bounded pieces while coordination is governed by explicit, machine-verifiable records.

---

## Why Shogi?

Shogi provides several structural mechanisms that map naturally to multi-agent orchestration.

| Shogi concept  | Agent-system interpretation                    |
| -------------- | ---------------------------------------------- |
| Piece          | Bounded agent role                             |
| Piece movement | Permitted operational scope                    |
| Board          | Mission environment                            |
| Square or node | Workflow, compute, data, or authority position |
| Legal move     | Policy-compliant state transition              |
| Capture        | Removal and controlled acquisition of an agent |
| Reserve piece  | Quarantined or reusable agent                  |
| Drop           | Authorized redeployment                        |
| Promotion      | Temporary capability expansion                 |
| Demotion       | Revocation and restoration of the base profile |
| King           | Protected human axis or mission authority      |
| Game record    | Traceable mission lifecycle                    |

The protocol uses these mechanics as structural primitives rather than as decorative terminology.

A piece is useful precisely because it is **not universal**. Its value comes from clearly defined movement, capability, authority, and resource boundaries.

---

## Design Goals

The protocol is designed to support the following properties:

1. **Role-bounded agency**
   Each agent operates within an explicit capability and authority profile.

2. **Position-aware execution**
   An agent's permitted actions depend on its current board position and zone.

3. **Default-deny movement**
   A move is denied unless a matching legal-move rule permits it.

4. **Human-axis protection**
   The protected human or mission authority cannot be captured, promoted, replaced, or rewritten by an autonomous agent.

5. **Traceable transitions**
   Every meaningful state change produces a machine-readable record.

6. **Safe agent reuse**
   Captured or withdrawn agents must pass through quarantine, sanitization, and re-authorization before redeployment.

7. **Reversible capability expansion**
   Promotion is temporary, bounded, expiring, and subject to demotion.

8. **Auditable mission closure**
   A mission cannot close while temporary authority, quarantine, unresolved authorization, or open disputes remain.

---

## Full Lifecycle

```text
Mission Profile
    ↓
Initial Board and Piece Placement
    ↓
Move Proposal
    ↓
Legal Move Evaluation
    ↓
Authorization / Human Review
    ↓
Move Receipt and Board Revision
    ↓
Capture
    ↓
Quarantine
    ↓
Sanitization
    ↓
Reserve Readiness
    ↓
Redeployment
    ↓
Promotion
    ↓
Temporary Capability Binding
    ↓
Demotion or Revocation
    ↓
Terminal Board Candidate
    ↓
Mission Termination Assessment
    ↓
Board Lifecycle Audit
    ↓
Shogi Agent Conformance Report
```

---

## Core Concepts

### Piece Profile

A piece profile defines the stable identity and base operating limits of an agent.

Typical fields include:

* piece type;
* agent identity;
* assigned side;
* allowed and denied actions;
* available tools;
* permitted data scopes;
* movement limits;
* authority scope;
* resource budget;
* human-axis binding;
* optional promotion constraints.

The base profile is treated as immutable during a mission. Temporary capability changes are expressed through separate promotion bindings.

### Board State

A board state represents a revisioned snapshot of the mission environment.

It records:

* mission identity;
* board revision;
* lifecycle status;
* zones and nodes;
* node adjacency;
* node capacity;
* active occupancies;
* reserve entries;
* human-axis reference;
* legal-move policy reference.

The board is not limited to a literal 9×9 grid. It may represent:

* workflow stages;
* compute nodes;
* data domains;
* security zones;
* organizational units;
* audit boundaries;
* settlement stages.

### Legal Move

A legal move is a state transition permitted by the current:

* piece profile;
* board state;
* source and target zones;
* adjacency graph;
* security boundary;
* authority scope;
* resource budget;
* legal-move policy;
* authorization and human-review requirements.

A declared `allow` decision is not trusted automatically. The validator independently recomputes the decision from the referenced records.

### Human-Axis King

The king represents the protected human authority, mission intent, or non-replaceable governance axis.

The human-axis king:

* cannot be captured;
* cannot be promoted;
* cannot be replaced by another agent;
* cannot be autonomously rewritten;
* must remain continuously bound across the mission lifecycle.

The king is not modeled as the most capable piece. It is modeled as the protected source of purpose and authority.

---

## Schema Set

### v0.1 — Foundation

Defines pieces, boards, placement, and legal movement.

```text
schemas/shogi-agent-piece-profile.schema.json
schemas/agent-board-state.schema.json
schemas/agent-placement-record.schema.json
schemas/legal-move-policy.schema.json
```

### v0.2 — Move Lifecycle

Defines move proposal, evaluation, and outcome recording.

```text
schemas/agent-move-proposal.schema.json
schemas/legal-move-evaluation.schema.json
schemas/agent-move-receipt.schema.json
```

### v0.3 — Capture and Reserve Lifecycle

Defines capture, quarantine, sanitization, reserve readiness, and redeployment.

```text
schemas/agent-capture-record.schema.json
schemas/agent-sanitization-assessment.schema.json
schemas/reserve-pool-entry.schema.json
schemas/agent-redeployment-record.schema.json
```

### v0.4 — Promotion Lifecycle

Defines temporary capability expansion and its revocation.

```text
schemas/agent-promotion-request.schema.json
schemas/promotion-eligibility-assessment.schema.json
schemas/promoted-capability-binding.schema.json
schemas/agent-demotion-record.schema.json
```

### v0.5 — Mission Closeout and Conformance

Defines mission intent, termination, lifecycle audit, and final conformance.

```text
schemas/shogi-agent-mission-profile.schema.json
schemas/mission-termination-assessment.schema.json
schemas/board-lifecycle-audit-record.schema.json
schemas/shogi-agent-conformance-report.schema.json
```

---

## Move Lifecycle

### Agent Move Proposal

The proposal records an intended move before execution.

It identifies:

* mission and board revision;
* piece and agent;
* source and target nodes;
* requested move kind;
* requested capabilities;
* estimated resource usage;
* applicable legal-move policy;
* proposal expiry;
* trace reference.

A proposal does not authorize execution.

### Legal Move Evaluation

The evaluation independently checks whether the proposed move is legal.

Typical checks include:

```text
mission_in_scope
board_reference_current
piece_registered
source_matches_board
target_available
route_reachable
within_piece_hop_limit
movement_rule_matched
security_boundary_allowed
restricted_zone_entry_allowed
resource_budget_satisfied
all_checks_passed
```

Possible decisions are:

```text
allow
human-review
deny
```

A denied move may be recorded as a valid decision, but it may not mutate the board.

### Agent Move Receipt

The receipt records the final outcome:

```text
executed
blocked
cancelled
expired
```

An executed move must resolve to:

* a valid proposal;
* a matching legal evaluation;
* required authorization;
* required human review;
* a placement record;
* consistent before-and-after board states;
* actual resource usage;
* execution timestamps;
* trace evidence.

---

## Capture, Sanitization, and Redeployment

> **Capture does not transfer trust.**

When an agent is captured or acquired, the receiving side gains management responsibility—not inherited trust.

The required lifecycle is:

```text
Capture
    ↓
Quarantine
    ↓
Inherited authority revoked
    ↓
Memory isolated
    ↓
Credentials revoked
    ↓
Tools detached
    ↓
Data scopes cleared
    ↓
Trace continuity verified
    ↓
Malware and policy checks completed
    ↓
Human review
    ↓
New bounded authority issued
    ↓
Reserve marked ready
    ↓
Redeployment
```

### Agent Capture Record

The capture record binds:

* the capturing piece;
* the captured piece;
* the executed move receipt;
* the capture location;
* before-and-after board states;
* original and receiving sides;
* quarantine destination;
* authorization;
* human review;
* trace evidence.

### Agent Sanitization Assessment

The assessment verifies:

```text
identity_verified
memory_isolated
credentials_revoked
tools_detached
data_scopes_cleared
trace_continuity_verified
malware_scan_passed
policy_conflicts_resolved
all_checks_passed
```

A declared `passed` decision is invalid if any required check is false.

### Reserve Pool Entry

Reserve entries are versioned records rather than mutable flags.

A captured agent initially enters a state such as:

```yaml
readiness_state: quarantined
authority_state: revoked
memory_state: isolated
assigned_side: neutral
```

Only after sanitization and re-authorization may it become:

```yaml
readiness_state: ready
authority_state: re-authorized
memory_state: sanitized
assigned_side: sente
```

### Agent Redeployment Record

Redeployment requires:

* a ready reserve entry;
* a passed sanitization assessment;
* a new bounded authority scope;
* matching side assignment;
* authorization;
* human review when required;
* a placement record;
* consistent board revisions.

---

## Promotion and Demotion

> **Promotion extends capability, not identity.**

Promotion is represented as a temporary delta over the immutable base piece profile.

It may temporarily add:

* capabilities;
* tools;
* data scopes;
* authority;
* resource limits;
* an effective promoted piece type.

Promotion may not:

* rewrite the human axis;
* override a base-denied action;
* exceed the piece's promotion profile;
* remain active without an expiry;
* survive mission closure.

### Promotion Lifecycle

```text
Agent Promotion Request
    ↓
Promotion Eligibility Assessment
    ↓
Authorization
    ↓
Human Review
    ↓
Promoted Capability Binding
    ↓
Temporary Promoted State
    ↓
Demotion, Expiry, or Revocation
    ↓
Base Profile Restored
```

### Promotion Eligibility

Typical checks include:

```text
piece_promotable
promotion_type_allowed
zone_allows_promotion
capabilities_allowed
tools_allowed
data_scopes_allowed
authority_scope_allowed
resource_multiplier_within_limit
duration_within_limit
no_active_promotion
human_axis_protected
```

Possible outcomes are:

```text
eligible
human-review
ineligible
```

### Promoted Capability Binding

Every active binding must include:

* exact granted capability delta;
* exact granted authority delta;
* effective time;
* mandatory expiry;
* authorization;
* human-review evidence when required;
* board and piece references.

### Demotion

Demotion must:

* target the same piece and binding;
* revoke the exact promoted delta;
* restore base authority;
* restore base resource limits;
* remove the promotion binding from the board;
* return the effective piece type to its base type.

---

## Mission Definition and Closure

### Shogi Agent Mission Profile

The mission profile fixes the mission before execution begins.

It defines:

* mission identity and objective;
* human-axis binding;
* initial board state;
* legal-move policy;
* success conditions;
* permitted termination conditions;
* required artifact types;
* maximum board revisions;
* mission timebox;
* conformance profile.

The profile prevents the system from changing the definition of success after the board has already moved.

### Mission Termination Assessment

The termination assessment determines whether the mission should:

```text
continue
complete
terminate
pause
human-review
```

It independently recomputes:

* required success-condition satisfaction;
* triggered termination conditions;
* terminal authorization;
* required human confirmation;
* active promotion bindings;
* quarantined reserve entries;
* unresolved authorization references;
* open dispute references;
* board status;
* timebox and revision limits.

A terminal outcome is invalid while any temporary or unresolved control remains open.

### Mission Closure Rule

```text
Task output exists
        ≠
Mission complete
```

A mission may close only when all required conditions hold:

```text
Required success conditions satisfied
+ Authorized terminal condition triggered
+ Human confirmation supplied when required
+ No active promotion binding
+ No quarantined reserve entry
+ No unresolved authorization
+ No open dispute
+ Final board marked completed or terminated
+ Lifecycle audit conformant
```

> **No mission closes with unresolved authority.**

---

## Board Lifecycle Audit

The lifecycle audit evaluates the complete revision chain rather than trusting a single final snapshot.

It verifies:

* contiguous board revisions;
* consistent mission identity;
* continuous human-axis binding;
* consistent legal-move policy;
* resolvable artifact references;
* authorization for executed moves;
* board immutability after denied moves;
* sanitization before redeployment;
* reversible promotion;
* demotion before closure;
* no active promotion at closure;
* no quarantined reserve at closure;
* terminal final-board status;
* trace references on transition records.

A valid final state cannot conceal an invalid intermediate transition.

---

## Conformance Report

The Shogi Agent Conformance Report binds together:

* the mission profile;
* the final board state;
* the termination assessment;
* the lifecycle audit.

Possible conformance states are:

```text
conformant
non-conformant
incomplete
```

Possible release decisions are:

```text
accepted
rejected
human-review
```

The report cannot override the independently recomputed results of the termination assessment or lifecycle audit.

---

## Canonical v0.5 Lineage

```text
board-state.wind-mission.0001
    ↓ initial placement and move lifecycle

board-state.wind-mission.0002
    ↓ captured silver enters quarantine

board-state.wind-mission.0003
    ↓ sanitized silver becomes ready reserve

board-state.wind-mission.0004
    ↓ silver is redeployed under new bounded authority

board-state.wind-mission.0005
    ↓ pawn receives a temporary promotion binding

board-state.wind-mission.0006
    ↓ promoted capability is revoked and base authority restored

board-state.wind-mission.0007
    ↓ terminal status: completed

termination-assessment.wind-mission.completed-0001
    ↓ success, authority, quarantine, and dispute checks

lifecycle-audit.wind-mission.0001
    ↓ full revision and artifact audit

conformance-report.wind-mission.0001
    └─ conformant / accepted
```

---

## Selected Semantic Invariants

1. The human-axis king cannot be captured, promoted, replaced, or autonomously rewritten.
2. No piece may act outside its capability, movement, security, authority, or resource bounds.
3. Every move is denied unless a matching policy rule permits it.
4. Denied moves may be recorded but may not mutate the board.
5. Every executed move requires the applicable authorization and human-review evidence.
6. Capture transfers custody, not trust.
7. Captured agents must enter quarantine.
8. Redeployment requires sanitization, re-authorization, and matching side assignment.
9. A failed sanitization check cannot produce a passed assessment.
10. Promotion grants only an approved and expiring delta over the base profile.
11. Promotion cannot grant a base-denied action.
12. Every promotion must be reversible.
13. Demotion must revoke the exact promoted delta and restore the base profile.
14. Mission success and termination rules must be fixed before execution.
15. A termination assessment must reference the current terminal board revision.
16. A mission cannot close with active promotions.
17. A mission cannot close with quarantined reserve entries.
18. A mission cannot close with unresolved authorizations or open disputes.
19. Board revisions in the lifecycle audit must be contiguous.
20. All lifecycle artifacts must preserve the same mission identity.
21. Every capture-to-redeployment chain must include a passed sanitization assessment.
22. Every referenced promotion binding must have a matching demotion before closure.
23. The final board must be `completed` or `terminated` before acceptance.
24. The conformance report cannot override non-conformant source records.
25. Every material transition must leave a trace.

---

## Validation

Install the required dependencies:

```bash
python -m pip install -r requirements.txt
```

Run the validator:

```bash
python scripts/validate_examples.py
```

The validator performs:

* JSON Schema validation;
* YAML parsing;
* identifier resolution;
* board and piece consistency checks;
* legal-move recomputation;
* authorization checks;
* resource-budget checks;
* capture and reserve lineage checks;
* sanitization checks;
* redeployment checks;
* promotion and demotion checks;
* board-revision continuity checks;
* mission-termination checks;
* final conformance checks.

Expected final output:

```text
All schemas, pass examples, and expected-fail examples validated successfully.
```

---

## Repository Structure

```text
shogi-agent-orchestration-protocol/
├── .github/
│   └── workflows/
│       └── validate.yml
├── examples/
│   ├── pass/
│   └── fail/
├── schemas/
├── scripts/
│   └── validate_examples.py
├── CHANGELOG.md
├── LICENSE
├── README.md
└── requirements.txt
```

### Directory Roles

| Path                             | Purpose                                                 |
| -------------------------------- | ------------------------------------------------------- |
| `schemas/`                       | JSON Schema definitions                                 |
| `examples/pass/`                 | Records expected to pass schema and semantic validation |
| `examples/fail/`                 | Records expected to fail for a declared reason          |
| `scripts/validate_examples.py`   | Cross-record validator                                  |
| `.github/workflows/validate.yml` | Continuous validation workflow                          |
| `CHANGELOG.md`                   | Version history                                         |
| `LICENSE`                        | License terms                                           |

---

## Version History

| Version | Scope                                                              |
| ------- | ------------------------------------------------------------------ |
| `v0.1`  | Piece profiles, board state, placement, and legal-move policy      |
| `v0.2`  | Move proposal, legal evaluation, and execution receipt             |
| `v0.3`  | Capture, quarantine, sanitization, reserve state, and redeployment |
| `v0.4`  | Promotion, temporary capability binding, demotion, and revocation  |
| `v0.5`  | Mission definition, termination, lifecycle audit, and conformance  |

The `v0.1`–`v0.5` line constitutes the first complete experimental core.

Future capabilities should be introduced through separate extension profiles or new repositories rather than expanding the core indefinitely.

Possible extension areas include:

* distributed multi-board federation;
* cross-mission piece transfer;
* tournament-style resource arbitration;
* adversarial-board threat modeling;
* economic settlement and royalty integration;
* formal interoperability profiles;
* reference orchestration runtimes.

---

## Non-Goals

This repository does not define:

* a complete production orchestration runtime;
* a universal agent framework;
* a model-training system;
* a replacement for existing identity or authorization standards;
* autonomous replacement of human authority;
* literal implementation of all traditional shogi rules.

The protocol uses selected shogi mechanics as governance and orchestration primitives.

---

## License

See [`LICENSE`](LICENSE).
