import { enumLabels, type LabelledEnum } from "./generated/labels";

export { enumLabels, enumValues, type LabelledEnum } from "./generated/labels";

/**
 * The spec's display label for an enum value. A value newer than this client (the server is
 * deployed first) shows as itself rather than blank.
 */
export function labelFor(schema: LabelledEnum, value: string): string {
  const labels: Readonly<Record<string, string>> = enumLabels[schema];
  return labels[value] ?? value;
}
