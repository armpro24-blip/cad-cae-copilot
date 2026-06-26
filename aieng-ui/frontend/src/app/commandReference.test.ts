import { describe, expect, it } from "vitest";
import { COMMAND_DOCS, COMMAND_KIND_LABEL, filterCommandDocs } from "./commandReference";

describe("commandReference content", () => {
  it("covers the five routed slash commands", () => {
    const commands = COMMAND_DOCS.map((d) => d.command);
    expect(commands).toEqual(["/build", "/modify", "/critique", "/explain", "/simulate"]);
  });

  it("every doc has a purpose and a copy-able example that starts with its command", () => {
    for (const doc of COMMAND_DOCS) {
      expect(doc.purpose.length).toBeGreaterThan(0);
      expect(doc.example.startsWith(doc.command)).toBe(true);
      expect(COMMAND_KIND_LABEL[doc.kind]).toBeTruthy();
    }
  });

  it("classifies mutation vs read-only vs simulate honestly", () => {
    const byCmd = Object.fromEntries(COMMAND_DOCS.map((d) => [d.command, d.kind]));
    expect(byCmd["/build"]).toBe("create");
    expect(byCmd["/modify"]).toBe("create");
    expect(byCmd["/critique"]).toBe("read");
    expect(byCmd["/explain"]).toBe("read");
    expect(byCmd["/simulate"]).toBe("simulate");
  });
});

describe("filterCommandDocs", () => {
  it("returns all docs for an empty query", () => {
    expect(filterCommandDocs("")).toHaveLength(COMMAND_DOCS.length);
    expect(filterCommandDocs("   ")).toHaveLength(COMMAND_DOCS.length);
  });

  it("matches on command name", () => {
    expect(filterCommandDocs("/simulate").map((d) => d.command)).toEqual(["/simulate"]);
  });

  it("matches on keyword (incl. non-English)", () => {
    expect(filterCommandDocs("stress").map((d) => d.command)).toContain("/simulate");
    expect(filterCommandDocs("审查").map((d) => d.command)).toContain("/critique");
  });

  it("is case-insensitive and matches on purpose text", () => {
    expect(filterCommandDocs("MANUFACTUR").map((d) => d.command)).toContain("/critique");
  });

  it("returns empty for a non-match", () => {
    expect(filterCommandDocs("zzzznomatch")).toEqual([]);
  });
});
