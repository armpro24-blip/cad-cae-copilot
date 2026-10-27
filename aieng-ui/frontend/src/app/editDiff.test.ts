import { expect, test } from "vitest";

import type { EditDiffResponse } from "../types";
import { shapeEditDiff } from "./editDiff";

function resp(overrides: Partial<EditDiffResponse>): EditDiffResponse {
  return { available: true, tool: "cad.edit_parameter", ...overrides };
}

test("unavailable / empty payloads yield no data", () => {
  expect(shapeEditDiff(null).hasData).toBe(false);
  expect(shapeEditDiff({ available: false }).hasData).toBe(false);
  // available but no diffs at all
  expect(shapeEditDiff(resp({})).hasData).toBe(false);
});

test("clean regression is good and needs no attention", () => {
  const view = shapeEditDiff(
    resp({
      regression_diff: {
        verdict: "clean",
        headline: "1 part changed as expected; 2 unchanged.",
        changed: [{ part: "base_plate", max_change_mm: 80, expected: true }],
        collateral_parts: [],
      },
    }),
  );
  expect(view.hasData).toBe(true);
  expect(view.regression?.tone).toBe("good");
  expect(view.regression?.changed[0]).toMatchObject({ name: "base_plate", collateral: false, maxChangeMm: 80 });
  expect(view.needsAttention).toBe(false);
});

test("collateral change is bad and marks the collateral part distinctly", () => {
  const view = shapeEditDiff(
    resp({
      regression_diff: {
        verdict: "collateral_change",
        changed: [
          { part: "arm_L", max_change_mm: 40, expected: true },
          { part: "torso", max_change_mm: 60, expected: false },
        ],
        collateral_parts: ["torso"],
      },
    }),
  );
  expect(view.regression?.tone).toBe("bad");
  expect(view.regression?.collateralCount).toBe(1);
  const torso = view.regression?.changed.find((c) => c.name === "torso");
  const arm = view.regression?.changed.find((c) => c.name === "arm_L");
  expect(torso?.collateral).toBe(true);
  expect(arm?.collateral).toBe(false);
  expect(view.needsAttention).toBe(true);
});

test("critique fail needs attention; improved/clean do not", () => {
  const failing = shapeEditDiff(
    resp({ critique_diff: { verdict: "fail", delta: { high: 1 }, introduced_count: 1 } }),
  );
  expect(failing.critique?.tone).toBe("bad");
  expect(failing.critique?.introducedCount).toBe(1);
  expect(failing.needsAttention).toBe(true);

  const improved = shapeEditDiff(resp({ critique_diff: { verdict: "improved", resolved_count: 2 } }));
  expect(improved.critique?.tone).toBe("good");
  expect(improved.critique?.resolvedCount).toBe(2);
  expect(improved.needsAttention).toBe(false);
});

test("topology_changed is a caution that needs attention", () => {
  const view = shapeEditDiff(
    resp({ regression_diff: { verdict: "topology_changed", added: ["rib"], removed: [] } }),
  );
  expect(view.regression?.tone).toBe("caution");
  expect(view.regression?.added).toEqual(["rib"]);
  expect(view.needsAttention).toBe(true);
});

test("geometry verification pass surfaces topology and export survival", () => {
  const view = shapeEditDiff(
    resp({
      geometry_verification: {
        topology_preserved: true,
        stale_reference_risk: false,
        topology_change: { topology_changed: false },
        face_edge_survival: {
          face: { before_count: 6, after_count: 6, survived_count: 6, added_count: 0, removed_count: 0 },
        },
        export_sanity: { step_exported: true, stl_exported: true, glb_exported: true, status: "pass", detail: "STEP and STL exports produced." },
        brep_validity: { status: "unknown", detail: "BRep validity not verified." },
      },
    }),
  );
  expect(view.hasData).toBe(true);
  expect(view.geometryVerification?.status).toBe("pass");
  expect(view.geometryVerification?.tone).toBe("good");
  expect(view.geometryVerification?.faceSurvival?.survived).toBe(6);
  expect(view.geometryVerification?.topologyPreserved).toBe(true);
  expect(view.needsAttention).toBe(false);
});

test("geometry verification warns when referenced face is lost", () => {
  const view = shapeEditDiff(
    resp({
      geometry_verification: {
        topology_preserved: false,
        stale_reference_risk: true,
        topology_change: { topology_changed: true, added_count: 1, removed_count: 1 },
        face_edge_survival: {
          face: {
            before_count: 6,
            after_count: 6,
            survived_count: 5,
            added_count: 1,
            removed_count: 1,
            referenced: [{ id: "face_003", status: "lost" }],
          },
        },
        export_sanity: { step_exported: true, stl_exported: true, glb_exported: true, status: "pass", detail: "" },
      },
    }),
  );
  expect(view.geometryVerification?.status).toBe("warn");
  expect(view.geometryVerification?.tone).toBe("caution");
  expect(view.geometryVerification?.faceSurvival?.referenced[0]).toMatchObject({
    id: "face_003",
    status: "lost",
  });
  expect(view.needsAttention).toBe(true);
});

test("geometry verification preserves unknown referenced-id status", () => {
  const view = shapeEditDiff(
    resp({
      geometry_verification: {
        topology_preserved: true,
        stale_reference_risk: false,
        topology_change: { topology_changed: false },
        face_edge_survival: {
          face: {
            before_count: 2,
            after_count: 2,
            survived_count: 2,
            added_count: 0,
            removed_count: 0,
            referenced: [{ id: "face_999", status: "unknown" }],
          },
        },
        export_sanity: {
          step_exported: true,
          stl_exported: true,
          glb_exported: true,
          status: "pass",
          detail: "",
        },
      },
    }),
  );
  expect(view.geometryVerification?.faceSurvival?.referenced[0]).toMatchObject({
    id: "face_999",
    status: "unknown",
  });
  expect(view.geometryVerification?.status).toBe("pass");
});

test("geometry verification absent when payload missing leaves view empty", () => {
  const view = shapeEditDiff(resp({}));
  expect(view.geometryVerification).toBeNull();
  expect(view.hasData).toBe(false);
});

test("geometry verification unknown when topology_preserved is missing", () => {
  const view = shapeEditDiff(
    resp({
      geometry_verification: {
        topology_preserved: undefined as unknown as boolean,
        stale_reference_risk: false,
        export_sanity: {
          step_exported: true,
          stl_exported: true,
          glb_exported: true,
          status: "pass",
          detail: "",
        },
      },
    }),
  );
  expect(view.geometryVerification?.status).toBe("unknown");
  expect(view.geometryVerification?.topologyPreserved).toBeNull();
});

test("an unknown face survival is not rendered as zero survived", () => {
  const view = shapeEditDiff(
    resp({
      geometry_verification: {
        topology_preserved: null as unknown as boolean,
        stale_reference_risk: null as unknown as boolean,
        face_edge_survival: {
          face: {
            status: "unknown",
            before_count: 16,
            after_count: 16,
            referenced: [{ id: "face_001", status: "unknown" }],
          } as never,
        },
        export_sanity: {
          step_exported: true,
          stl_exported: true,
          glb_exported: true,
          status: "pass",
          detail: "",
        },
      },
    }),
  );
  expect(view.geometryVerification?.status).toBe("unknown");
  expect(view.geometryVerification?.faceSurvival).toBeNull();
});
