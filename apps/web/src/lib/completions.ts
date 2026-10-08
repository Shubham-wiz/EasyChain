// Flow Data suggestions for the Python editor of Code steps (MonacoEditor.tsx uses these).

export interface FlowDataCompletion<F> {
  field: F;
  /** What the list shows, and exactly what accepting it writes. */
  label: string;
  /** How many characters before the cursor it replaces (what was typed of it). */
  before: number;
  /** How many characters after the cursor it replaces (closing quotes and brackets the editor added). */
  after: number;
}

const ESCAPE = /[.*+?^${}()|[\]\\]/g;

/**
 * Suggestions for the text around the cursor: after `data[` or `data["na` they complete the
 * key (`data["name"]`), after `data.get(` the call (`data.get("name")`), and while typing a
 * word (`da`) they write `data["name"]`. Each replaces what was typed and the closing
 * characters the editor added, so the result reads exactly like the label. Anywhere else
 * (after a quote or a bracket that isn't `data[`) there are none.
 */
export function flowDataCompletions<F extends { name: string }>(before: string, after: string, fields: F[]): FlowDataCompletion<F>[] {
  for (const [open, close] of [
    ["data[", "]"],
    ["data.get(", ")"],
  ]) {
    const m = new RegExp(`\\bdata${open.slice(4).replace(ESCAPE, "\\$&")}\\s*(["']?)([A-Za-z0-9_]*)$`).exec(before);
    if (!m) continue;
    const quote = m[1] || '"';
    let end = 0;
    if (m[1] && after[end] === m[1]) end += 1;
    if (after[end] === close) end += 1;
    return fields.map((field) => ({ field, label: `${open}${quote}${field.name}${quote}${close}`, before: m[0].length, after: end }));
  }
  const word = /[A-Za-z_][A-Za-z0-9_]*$/.exec(before)?.[0] ?? "";
  const prev = before.charAt(before.length - word.length - 1);
  if (prev && /[\w."'[\]]/.test(prev)) return [];
  // Right after "(", "," and the like (typing, not a word yet): nothing to offer.
  if (!word && prev && !/\s/.test(prev)) return [];
  return fields.map((field) => ({ field, label: `data["${field.name}"]`, before: word.length, after: 0 }));
}
