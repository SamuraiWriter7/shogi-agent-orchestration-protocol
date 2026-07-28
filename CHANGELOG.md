# Changelog

All notable changes to the Shogi Agent Orchestration Protocol are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows semantic versioning during its experimental `v0.x` development line.

---

## [0.5.0] - 2026-07-28

### Added

* Added the `Shogi Agent Mission Profile` schema for fixing the following before execution:

  * mission identity;
  * mission objective;
  * human-axis binding;
  * initial board state;
  * legal-move policy;
  * success conditions;
  * permitted termination conditions;
  * required artifact classes;
  * maximum board revisions;
  * mission timebox;
  * conformance profile.
* Added the `Mission Termination Assessment` schema for independently recomputing:

  * `continue`;
  * `complete`;
  * `terminate`;
  * `pause`;
  * `human-review`.
* Added the `Board Lifecycle Audit Record` schema for validating:

  * the complete board-revision chain;
  * mission identity continuity;
  * human-axis continuity;
  * legal-move policy continuity;
  * executed-move authorization;
  * capture lineage;
  * sanitization before redeployment;
  * promotion and demotion lineage;
  * terminal-board readiness;
  * transition trace references.
* Added the `Shogi Agent Conformance Report` schema for producing a final machine-readable:

  * conformance status;
  * release decision;
  * closeout summary.
* Added a terminal board revision demonstrating authorized mission completion after:

  * all temporary promotion bindings were revoked;
  * all captured agents exited quarantine;
  * all required authorizations were resolved;
  * the final board was marked `completed`.
* Added canonical mission-closeout records connecting:

  * the mission profile;
  * the terminal board;
  * the termination assessment;
  * the lifecycle audit;
  * the final conformance report.
* Added expected-fail examples for:

  * a mission profile referencing the wrong legal-move policy;
  * mission completion while a promotion remains active;
  * a lifecycle audit with a missing board revision;
  * a report falsely declaring conformance.

### Changed

* Advanced active schemas and examples to:

  ```yaml
  schema_version: 0.5.0
  ```

* Advanced the legal-move policy identifier and version to `v0.5`.

* Extended the validator from individual transition checks to end-to-end mission-closeout validation.

* Defined mission completion as an audited lifecycle state rather than the mere production of a task result.

* Added independent recomputation of:

  * mission success;
  * triggered termination conditions;
  * active temporary authority;
  * unresolved authorization;
  * quarantine status;
  * dispute status;
  * terminal-board readiness.

* Added full board-revision continuity validation from the declared initial revision to the final revision.

* Added final conformance resolution across all v0.1–v0.5 lifecycle records.

### Security

* A mission cannot close while any promotion binding remains active.
* A mission cannot close while any reserve entry remains quarantined.
* A mission cannot close while authorization references remain unresolved.
* A mission cannot close while dispute references remain open.
* Terminal outcomes require explicit authorization.
* Terminal outcomes require human confirmation when required by the triggered termination condition.
* The lifecycle audit verifies continuous human-axis binding across every declared board revision.
* The lifecycle audit rejects missing, duplicated, reordered, or inconsistent board revisions.
* A conformance report cannot declare acceptance when:

  * its termination assessment is non-conformant;
  * its lifecycle audit is non-conformant;
  * its final board is not terminal;
  * required source artifacts cannot be resolved.
* Final release decisions cannot override independently recomputed source-record results.

---

## [0.4.0] - 2026-07-27

### Added

* Added the `Agent Promotion Request` schema for proposing temporary expansion of:

  * capabilities;
  * tools;
  * data scopes;
  * authority scope;
  * resource limits;
  * effective piece type;
  * promotion duration.
* Added the `Promotion Eligibility Assessment` schema with independently recomputed checks for:

  * piece promotability;
  * permitted promoted piece type;
  * promotion-zone eligibility;
  * capability additions;
  * tool additions;
  * data-scope additions;
  * authority-scope additions;
  * resource multiplier;
  * duration limit;
  * existing active promotion;
  * human-axis protection.
* Added the `Promoted Capability Binding` schema for authorized, human-governed, and expiring capability deltas.
* Added the `Agent Demotion Record` schema for:

  * revoking promoted additions;
  * restoring base capabilities;
  * restoring base authority;
  * restoring base resource limits;
  * returning the effective piece type to its base type.
* Added optional `promotion_profile` constraints to promotable piece profiles.
* Added promotion-aware board occupancy fields:

  * `promotion_state`;
  * `effective_piece_type`;
  * `promotion_binding_ref`.
* Added board revisions demonstrating:

  * temporary promotion;
  * active promoted operation;
  * demotion;
  * base-profile restoration.
* Added expected-fail examples for:

  * attempting to promote the human-axis king;
  * falsely declaring an ineligible promotion eligible;
  * granting capabilities beyond the approved promotion delta;
  * exceeding the maximum resource multiplier;
  * exceeding the approved duration;
  * demoting a piece using a mismatched promotion binding.

### Changed

* Advanced active schemas and examples to:

  ```yaml
  schema_version: 0.4.0
  ```

* Advanced the legal-move policy identifier and version to `v0.4`.

* Extended the validator with promotion and demotion lineage checks.

* Defined promotion as a temporary delta over an immutable base piece profile.

* Added board validation for:

  * promoted occupancy state;
  * effective piece type;
  * active binding reference;
  * demoted occupancy state.

* Added exact-delta comparison between:

  * promotion request;
  * eligibility assessment;
  * active capability binding;
  * demotion record.

### Security

* Promotion cannot grant `rewrite_human_axis`.
* Promotion cannot grant an action explicitly denied by the base profile.
* The human-axis king is structurally non-promotable.
* Gold pieces are structurally non-promotable in the canonical policy.
* Every active promotion requires explicit authorization.
* Every active promotion requires a mandatory expiry.
* Human review is enforced when required by the piece promotion profile.
* A promotion binding cannot exceed:

  * approved capabilities;
  * approved tools;
  * approved data scopes;
  * approved authority;
  * approved resource limits;
  * approved duration.
* Demotion must revoke the exact granted delta.
* Demotion must restore the original authority and resource limits.
* An active promotion cannot survive mission closure.

---

## [0.3.0] - 2026-07-27

### Added

* Added the `Agent Capture Record` schema for binding a capture to:

  * an executed move receipt;
  * the capturing piece;
  * the captured piece;
  * the capture node;
  * before-and-after board states;
  * original and receiving sides;
  * quarantine destination;
  * authorization;
  * human review;
  * trace evidence.
* Added the `Agent Sanitization Assessment` schema for validating:

  * identity;
  * memory isolation;
  * credential revocation;
  * tool detachment;
  * data-scope clearing;
  * trace continuity;
  * malware scanning;
  * policy-conflict resolution.
* Added versioned `Reserve Pool Entry` records for:

  * quarantined state;
  * review-required state;
  * ready state;
  * blocked state.
* Added the `Agent Redeployment Record` schema for the authorized return of a sanitized reserve agent to the board.
* Added quarantine-zone and quarantine-node support to the canonical board model.
* Added a captured external silver-agent profile.
* Added a full capture-to-redeployment lineage.
* Added board revisions demonstrating:

  * capture;
  * quarantine;
  * sanitization;
  * ready reserve state;
  * re-authorization;
  * controlled redeployment.
* Added expected-fail examples for:

  * capture of the human-axis king;
  * a false sanitization pass;
  * an unsafe reserve entry marked ready;
  * a redeployment-side mismatch.
* Added cross-board semantic validation across revisions 1 through 4.

### Changed

* Advanced active schemas and examples to:

  ```yaml
  schema_version: 0.3.0
  ```

* Advanced the legal-move policy identifier and version to `v0.3`.

* Added `capture` to move kinds.

* Added `redeployment` to placement kinds.

* Expanded reserve entries with:

  * immutable record references;
  * source type;
  * effective side;
  * readiness evidence;
  * sanitization reference;
  * authorization reference.

* Extended the validator to resolve lifecycle records against the exact board revision they reference.

* Added cross-record checks between:

  * capture record;
  * sanitization assessment;
  * reserve entry;
  * redeployment record;
  * placement record;
  * board state.

### Security

* Capture transfers custody, not trust.
* Captured agents are never trusted automatically.
* Captured agents are never redeployed automatically.
* Captured agents must enter quarantine.
* Reuse requires:

  * inherited-authority revocation;
  * memory isolation;
  * credential revocation;
  * tool detachment;
  * data-scope clearing;
  * sanitization;
  * explicit re-authorization;
  * human review when required.
* A failed sanitization check cannot produce a passed decision.
* A reserve agent cannot be marked ready without valid sanitization evidence.
* The human-axis king is structurally non-capturable.
* Redeployment must use the newly assigned side and authority scope.

---

## [0.2.0] - 2026-07-27

### Added

* Added the `Agent Move Proposal` schema for bounded move requests against a fixed board revision.
* Added the `Legal Move Evaluation` schema for reproducible:

  * `allow`;
  * `human-review`;
  * `deny`.
* Added the `Agent Move Receipt` schema for:

  * `executed`;
  * `blocked`;
  * `cancelled`;
  * `expired`.
* Added pass examples covering:

  * allowed execution;
  * human-reviewed execution;
  * a valid denial;
  * a blocked receipt after denial.
* Added expected-fail examples for:

  * stale board revisions;
  * a false `allow` decision;
  * missing authorization;
  * execution after denial.
* Added cross-document semantic validation for:

  * proposals;
  * evaluations;
  * placement records;
  * move receipts.

### Changed

* Advanced active schemas and examples to:

  ```yaml
  schema_version: 0.2.0
  ```

* Advanced the legal-move policy identifier and version to `v0.2`.

* Extended placement records with optional:

  * proposal reference;
  * evaluation reference.

* Updated CI validation output for the v0.2 move lifecycle.

* Extended the validator to independently recompute:

  * board-revision freshness;
  * source occupancy;
  * target availability;
  * route reachability;
  * piece hop limit;
  * matching movement rule;
  * security-boundary permission;
  * restricted-zone entry;
  * resource-budget compliance.

* Added consistency validation between:

  * the declared decision;
  * recomputed decision;
  * matched rule;
  * required controls;
  * reason codes.

### Security

* Writing `decision: allow` is insufficient to authorize a move.
* The validator independently recomputes the legal-move decision.
* A denied move may be recorded but may not mutate the board.
* An executed receipt requires a matching authorization reference.
* Human-review evidence is required when the matched movement rule or target zone requires it.
* An expired or stale proposal cannot authorize execution.
* A move receipt cannot contradict its referenced evaluation.
* A placement record cannot reference an unknown piece or an illegal transition.

---

## [0.1.0] - 2026-07-27

### Added

* Created the initial Shogi Agent Orchestration Protocol repository.
* Added the `Shogi Agent Piece Profile` JSON Schema.
* Added the `Agent Board State` JSON Schema.
* Added the `Agent Placement Record` JSON Schema.
* Added the `Legal Move Policy` JSON Schema.
* Added canonical piece types:

  * `king`;
  * `rook`;
  * `bishop`;
  * `gold`;
  * `silver`;
  * `knight`;
  * `lance`;
  * `pawn`;
  * `custom`.
* Added a graph-based board model using:

  * zones;
  * nodes;
  * node adjacency;
  * node capacity;
  * occupancies;
  * reserve state.
* Added human-axis binding.
* Added the active human-axis king invariant.
* Added piece-level definitions for:

  * capabilities;
  * denied actions;
  * tools;
  * data scopes;
  * authority scope;
  * movement scope;
  * resource budgets.
* Added initial-placement rules.
* Added movement-policy rules.
* Added pass examples for:

  * king;
  * pawn;
  * gold;
  * board state;
  * legal-move policy;
  * placement record.
* Added expected-fail examples for:

  * invalid piece types;
  * duplicate occupancy;
  * unknown pieces;
  * illegal transitions.
* Added a Python validator for:

  * JSON Schema validation;
  * YAML parsing;
  * cross-record semantic validation.
* Added a GitHub Actions workflow for continuous validation.

### Security

* Added a default-deny legal-move policy.
* Prevented non-king pieces from autonomously rewriting the human axis.
* Added restricted-zone movement checks.
* Added security-boundary movement checks.
* Added authorization-reference checks.
* Added human-review-reference checks.
* Added node-capacity validation.
* Added identity consistency checks.
* Added lifecycle-state checks.
* Added source-position and target-position consistency checks.

---

## Version Summary

| Version | Primary scope                                                      |
| ------- | ------------------------------------------------------------------ |
| `0.1.0` | Piece profiles, board state, placement, and legal movement         |
| `0.2.0` | Move proposal, evaluation, authorization, and receipt              |
| `0.3.0` | Capture, quarantine, sanitization, reserve state, and redeployment |
| `0.4.0` | Promotion, temporary capability binding, demotion, and revocation  |
| `0.5.0` | Mission definition, termination, lifecycle audit, and conformance  |

The `v0.1`–`v0.5` series completes the first experimental core of the protocol.

Future capabilities should be developed through extension profiles or separate repositories rather than expanding the core without limit.
