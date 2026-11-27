import { describe, expect, it } from "vitest";
import { enumLabels, enumValues, labelFor } from "./index";

describe("generated enum labels", () => {
  it("label every TaskColumn value, in spec order", () => {
    expect(enumValues.TaskColumn[0]).toBe("backlog");
    expect(Object.keys(enumLabels.TaskColumn)).toEqual([...enumValues.TaskColumn]);
    expect(enumLabels.TaskColumn.out_of_scope).toBe("Out of scope");
  });

  it("labelFor falls back to the raw value for a value the client doesn't know yet", () => {
    expect(labelFor("TaskColumn", "changes_requested")).toBe("Changes requested");
    expect(labelFor("TaskColumn", "someday")).toBe("someday");
  });
});
