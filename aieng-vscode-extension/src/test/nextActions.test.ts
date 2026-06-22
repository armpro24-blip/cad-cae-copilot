import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  formatActionDetail,
  parseNextActions,
  toHandoffPrompt,
  toToolCallSnippet,
} from "../nextActions";

const READY_RESPONSE = {
  ok: true,
  ready_to_run: false,
  next_actions: [
    {
      id: "act_add_material",
      label: "Add a material to the CAE setup",
      tool: "cae.apply_setup_patch",
      input: { project_id: "p1" },
      priority: "high",
      available_now: true,
      requires_approval: false,
      mutates_package: true,
      runs_solver: false,
      advances_claim: false,
    },
    {
      id: "act_run_solver",
      label: "Run the CalculiX solver",
      tool: "cae.run_solver",
      input: { project_id: "p1" },
      priority: "medium",
      available_now: false,
      blocked_reason: "Required inputs are missing.",
      blocked_reason_codes: ["missing_material", "approval_required"],
      requires_approval: true,
      mutates_package: true,
      runs_solver: true,
      advances_claim: false,
    },
  ],
};

describe("parseNextActions", () => {
  it("returns [] for junk / missing", () => {
    assert.deepEqual(parseNextActions(null), []);
    assert.deepEqual(parseNextActions({}), []);
    assert.deepEqual(parseNextActions({ next_actions: "nope" }), []);
  });

  it("extracts and normalizes top-level next_actions", () => {
    const actions = parseNextActions(READY_RESPONSE);
    assert.equal(actions.length, 2);
    const [add, run] = actions;
    assert.equal(add.tool, "cae.apply_setup_patch");
    assert.equal(add.availableNow, true);
    assert.equal(add.mutatesPackage, true);
    assert.equal(run.availableNow, false);
    assert.equal(run.blockedReason, "Required inputs are missing.");
    assert.deepEqual(run.blockedReasonCodes, ["missing_material", "approval_required"]);
    assert.equal(run.requiresApproval, true);
    assert.equal(run.runsSolver, true);
  });

  it("falls back to receipt.next_actions when top-level is absent", () => {
    const actions = parseNextActions({ receipt: { next_actions: READY_RESPONSE.next_actions } });
    assert.equal(actions.length, 2);
    assert.equal(actions[0].id, "act_add_material");
  });

  it("defaults availableNow to true only when not explicitly blocked", () => {
    const actions = parseNextActions({ next_actions: [{ tool: "x.y", label: "L" }] });
    assert.equal(actions[0].availableNow, true);
    assert.deepEqual(actions[0].blockedReasonCodes, []);
  });
});

describe("formatActionDetail (safety flags preserved in display text)", () => {
  it("marks a blocked action as blocked with reason and codes", () => {
    const [, run] = parseNextActions(READY_RESPONSE);
    const detail = formatActionDetail(run);
    assert.match(detail, /[Bb]locked/);
    assert.match(detail, /Required inputs are missing\./);
    assert.match(detail, /missing_material/);
    assert.match(detail, /approval/i);
    assert.match(detail, /solver/i);
  });

  it("marks an available action as available and keeps its flags", () => {
    const [add] = parseNextActions(READY_RESPONSE);
    const detail = formatActionDetail(add);
    assert.match(detail, /[Aa]vailable/);
    assert.match(detail, /mutates/i);
  });
});

describe("toToolCallSnippet", () => {
  it("emits a valid invoke-tool JSON body with tool + input", () => {
    const [add] = parseNextActions(READY_RESPONSE);
    const snippet = toToolCallSnippet(add);
    const parsed = JSON.parse(snippet);
    assert.equal(parsed.tool, "cae.apply_setup_patch");
    assert.deepEqual(parsed.input, { project_id: "p1" });
  });
});

describe("toHandoffPrompt", () => {
  it("includes label, tool, and preserves blocked + safety status", () => {
    const [, run] = parseNextActions(READY_RESPONSE);
    const prompt = toHandoffPrompt(run);
    assert.match(prompt, /Run the CalculiX solver/);
    assert.match(prompt, /cae\.run_solver/);
    assert.match(prompt, /blocked/i);
    assert.match(prompt, /approval/i);
    assert.match(prompt, /solver/i);
  });

  it("does not imply auto-execution", () => {
    const [add] = parseNextActions(READY_RESPONSE);
    const prompt = toHandoffPrompt(add).toLowerCase();
    // copy-only handoff: never an executed/ran claim
    assert.ok(!/\b(executed|has run|already ran)\b/.test(prompt));
  });
});
