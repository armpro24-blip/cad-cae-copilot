// Command reference content (#398): the routed `/command` vocabulary, so a new
// user can discover what to type to their agent from within the app instead of
// guessing. Pure data + a filter helper — the CommandReference component renders
// it. Kept in sync with the backend INTENT_REGISTRY (engine.py) /
// COMPOSER_COMMANDS (composerIntent.ts).

export type CommandKind = "create" | "read" | "simulate";

export interface CommandDoc {
  /** The slash command, e.g. "/modify". */
  command: string;
  /** One-line purpose, written from the user's side of the screen. */
  purpose: string;
  /** A concrete, copy-able example. */
  example: string;
  /** Coarse category — drives the badge + grouping. */
  kind: CommandKind;
  /** Search terms beyond the command + purpose (English + 中文). */
  keywords: string[];
}

export const COMMAND_KIND_LABEL: Record<CommandKind, string> = {
  create: "Changes geometry",
  read: "Read-only",
  simulate: "Simulation",
};

export const COMMAND_DOCS: ReadonlyArray<CommandDoc> = [
  {
    command: "/build",
    purpose: "Create new geometry from a description.",
    example: "/build a 120×80×8 mm aluminium bracket with four M6 mounting holes",
    kind: "create",
    keywords: ["create", "make", "generate", "new", "model", "建模", "生成"],
  },
  {
    command: "/modify",
    purpose: "Change an existing model — resize a dimension, add or remove a feature.",
    example: "/modify set wall thickness to 5 mm",
    kind: "create",
    keywords: ["edit", "change", "resize", "set", "adjust", "修改", "调整"],
  },
  {
    command: "/critique",
    purpose: "Get a manufacturability / design audit of the current model. Changes nothing.",
    example: "/critique check this part for CNC machining",
    kind: "read",
    keywords: ["review", "audit", "check", "dfm", "manufacturability", "审查"],
  },
  {
    command: "/explain",
    purpose: "Have the agent explain the project, a part, or a result. Changes nothing.",
    example: "/explain what does this model do",
    kind: "read",
    keywords: ["describe", "what", "how", "understand", "解释"],
  },
  {
    command: "/simulate",
    purpose: "Set up and run structural FEA — material, loads, constraints, solver.",
    example: "/simulate apply a 500 N load on the top face and fix the base",
    kind: "simulate",
    keywords: ["fea", "solve", "stress", "load", "analysis", "solver", "仿真", "分析"],
  },
];

/** Filter the command docs by a free-text query (command, purpose, keywords). */
export function filterCommandDocs(query: string): CommandDoc[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...COMMAND_DOCS];
  return COMMAND_DOCS.filter((doc) => {
    const haystack = [doc.command, doc.purpose, doc.example, ...doc.keywords].join(" ").toLowerCase();
    return haystack.includes(q);
  });
}
