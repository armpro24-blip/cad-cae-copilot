import { describe, expect, it } from "vitest";
import { humanizeApiError } from "./apiError";

describe("humanizeApiError", () => {
  it("extracts FastAPI {detail} strings", () => {
    expect(humanizeApiError('{"detail":"Material catalog is empty"}')).toBe("Material catalog is empty");
  });

  it("falls back for noise details (Not Found / Internal Server Error)", () => {
    expect(humanizeApiError('{"detail":"Not Found"}', "Couldn't load.")).toBe("Couldn't load.");
    expect(humanizeApiError('{"detail":"Internal Server Error"}', "Couldn't load.")).toBe("Couldn't load.");
    expect(humanizeApiError("Not Found", "Couldn't load.")).toBe("Couldn't load.");
  });

  it("flattens 422-style detail arrays", () => {
    const raw = '{"detail":[{"loc":["body","x"],"msg":"field required"},{"msg":"too small"}]}';
    expect(humanizeApiError(raw)).toBe("field required; too small");
  });

  it("accepts an Error instance and reads its message", () => {
    expect(humanizeApiError(new Error('{"detail":"boom"}'))).toBe("boom");
  });

  it("passes through a plain human message unchanged", () => {
    expect(humanizeApiError("Insert failed: part not editable")).toBe("Insert failed: part not editable");
  });

  it("uses the fallback for empty / nullish input", () => {
    expect(humanizeApiError("", "fallback")).toBe("fallback");
    expect(humanizeApiError(null, "fallback")).toBe("fallback");
    expect(humanizeApiError(undefined, "fallback")).toBe("fallback");
  });

  it("returns non-JSON braces text as-is rather than crashing", () => {
    expect(humanizeApiError("{not json")).toBe("{not json");
  });
});
