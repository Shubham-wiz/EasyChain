// Inputs edited in rows and inputs that save on leaving them: they always show the saved value.

import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import type { FlowSpec, FormField, McpSettings } from "../lib/types";
import { undo, useFlow } from "../state/flow";
import { NoteNode } from "./canvas/NoteNode";
import { McpSection } from "./dialogs/McpServers";
import { InputFieldsEditor, KeyValueEditor, type FieldProps } from "./inspector/fields";
import { SchemaBuilder } from "./inspector/SchemaBuilder";
import { Field, Input, SecretInput } from "./ui";

vi.mock("../lib/api", async (original) => ({
  ...(await original<typeof import("../lib/api")>()),
  api: { mcpSettings: vi.fn(), saveMcpSettings: vi.fn(), mcpTools: vi.fn() },
}));

const mocked = api as unknown as Record<"mcpSettings" | "saveMcpSettings" | "mcpTools", ReturnType<typeof vi.fn>>;

/** Renders a settings control the way the inspector does: its value lives in the parent. */
function Harness({ control: Control, field, initial, onChange }: { control: (p: FieldProps) => React.ReactNode; field: Partial<FormField>; initial: unknown; onChange?: (v: unknown) => void }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <Control
        field={{ key: "k", label: "Setting", kind: "x", ...field } as FormField}
        value={value}
        onChange={(v) => {
          onChange?.(v);
          setValue(v);
        }}
        settings={{}}
        fields={[]}
        id="setting"
      />
      <button type="button" onClick={() => setValue(initial)}>
        Put back
      </button>
    </>
  );
}

const names = (label: string) => screen.getAllByLabelText(label).map((el) => (el as HTMLInputElement).value);

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("rows of a list", () => {
  const inputs = [
    { name: "first", type: "text", description: "", example: null, required: true },
    { name: "second", type: "text", description: "", example: null, required: true },
  ];

  it("removing a row doesn't rename the next one", () => {
    const onChange = vi.fn();
    render(<Harness control={InputFieldsEditor} field={{ options: [{ value: "text", label: "Text" }] }} initial={inputs} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove field first" }));
    expect(names("Field name")).toEqual(["second"]);
    const [input] = screen.getAllByLabelText("Field name");
    fireEvent.focus(input);
    fireEvent.blur(input);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0].map((f: { name: string }) => f.name)).toEqual(["second"]);
  });

  it("a renamed field shows the saved name again after undo", () => {
    render(<Harness control={InputFieldsEditor} field={{ options: [{ value: "text", label: "Text" }] }} initial={inputs} />);
    const [input] = screen.getAllByLabelText("Field name");
    fireEvent.change(input, { target: { value: "Your Name" } });
    fireEvent.blur(input);
    expect(names("Field name")).toEqual(["your_name", "second"]);
    fireEvent.click(screen.getByRole("button", { name: "Put back" }));
    expect(names("Field name")).toEqual(["first", "second"]);
  });

  it("key-value rows keep their keys straight", () => {
    render(<Harness control={KeyValueEditor} field={{ key: "headers" }} initial={{ "X-One": "1", "X-Two": "2" }} />);
    fireEvent.click(screen.getAllByRole("button", { name: "Remove header name" })[0]);
    expect(names("Header name")).toEqual(["X-Two"]);
    expect(names("Header value (X-Two)")).toEqual(["2"]);
  });

  it("reply format fields: removing one leaves the others' names and choices", () => {
    const output = {
      fields: [
        { name: "mood", type: "choice", options: ["happy", "sad"] },
        { name: "reason", type: "choice", options: ["price", "service"] },
      ],
    };
    const onChange = vi.fn();
    render(<Harness control={SchemaBuilder} field={{ label: "Reply format" }} initial={output} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove mood" }));
    expect(names("Field name")).toEqual(["reason"]);
    expect(names("Choices")).toEqual(["price, service"]);
    fireEvent.blur(screen.getByLabelText("Field name"));
    fireEvent.blur(screen.getByLabelText("Choices"));
    expect(onChange).toHaveBeenCalledTimes(1);
  });
});

describe("sticky notes", () => {
  const spec: FlowSpec = {
    version: 1,
    name: "Notes",
    description: "",
    data: [],
    steps: [],
    connections: [],
    canvas: { steps: {}, notes: [{ id: "n1", text: "Before", x: 0, y: 0, width: 200, height: 100 }] },
  };

  it("follow undo", () => {
    useFlow.getState().load("f", spec);
    render(
      <ReactFlowProvider>
        <NoteNode {...({ id: "note:n1", selected: false } as NodeProps)} />
      </ReactFlowProvider>,
    );
    const note = screen.getByLabelText("Sticky note");
    fireEvent.change(note, { target: { value: "After" } });
    fireEvent.blur(note);
    expect(useFlow.getState().spec!.canvas.notes[0].text).toBe("After");
    act(() => undo());
    expect(note).toHaveValue("Before");
  });
});

describe("MCP servers", () => {
  const settings: McpSettings = {
    servers: [
      { id: "docs", name: "Docs", transport: "http", url: "https://docs/mcp", headers: { Authorization: "••••••" }, command: "", args: [], env: {} },
      { id: "maps", name: "Maps", transport: "http", url: "https://maps/mcp", headers: {}, command: "", args: [], env: {} },
    ],
    allowed_commands: [],
  } as McpSettings;

  it("a card keeps its own tools and id when the one above is removed", async () => {
    mocked.mcpSettings.mockResolvedValue(structuredClone(settings));
    mocked.mcpTools.mockResolvedValue({ tools: [{ name: "search_docs", description: "" }] });
    render(<McpSection />);
    const docs = await screen.findByTestId("mcp-server-docs");
    fireEvent.click(within(docs).getByRole("button", { name: "Show its tools" }));
    await within(docs).findByText("search_docs");
    fireEvent.click(screen.getByRole("button", { name: "Remove Docs" }));
    const maps = screen.getByTestId("mcp-server-maps");
    expect(within(maps).queryByText("search_docs")).toBeNull();
    expect(within(maps).getByLabelText("Server id")).toHaveValue("maps");
  });

  it("a hidden Authorization header is shown as saved and sent back unchanged", async () => {
    mocked.mcpSettings.mockResolvedValue(structuredClone(settings));
    mocked.saveMcpSettings.mockImplementation(async (body: McpSettings) => body);
    render(<McpSection />);
    const docs = await screen.findByTestId("mcp-server-docs");
    const header = within(docs).getByLabelText("Authorization header");
    expect(header).toHaveValue("");
    expect(header).toHaveAttribute("type", "password");
    expect(header.getAttribute("placeholder")).toMatch(/saved/i);
    fireEvent.click(screen.getByRole("button", { name: "Save MCP servers" }));
    await waitFor(() => expect(mocked.saveMcpSettings).toHaveBeenCalled());
    expect(mocked.saveMcpSettings.mock.calls[0][0].servers[0].headers).toEqual({ Authorization: "••••••" });
  });
});

describe("secret inputs", () => {
  function Secret({ initial, onChange }: { initial: string; onChange: (v: string) => void }) {
    const [value, setValue] = useState(initial);
    return (
      <SecretInput
        aria-label="Password"
        value={value}
        onChange={(v) => {
          onChange(v);
          setValue(v);
        }}
      />
    );
  }

  it("hide what is typed unless asked, and nudge towards {secret:NAME}", () => {
    const onChange = vi.fn();
    render(<Secret initial="" onChange={onChange} />);
    const input = screen.getByLabelText("Password");
    expect(input).toHaveAttribute("type", "password");
    fireEvent.change(input, { target: { value: "hunter2" } });
    expect(input).toHaveAccessibleDescription(/\{secret:NAME\}/);
    fireEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(input).toHaveAttribute("type", "text");
    fireEvent.change(input, { target: { value: "{secret:SMTP_PASSWORD}" } });
    expect(input).not.toHaveAccessibleDescription(/\{secret:NAME\}/);
  });

  it("treat a hidden saved value as unchanged until something new is typed", () => {
    const onChange = vi.fn();
    render(<Secret initial="••••••" onChange={onChange} />);
    const input = screen.getByLabelText("Password");
    expect(input).toHaveValue("");
    fireEvent.change(input, { target: { value: "new" } });
    expect(onChange).toHaveBeenLastCalledWith("new");
    fireEvent.change(input, { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith("••••••");
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(onChange).toHaveBeenLastCalledWith("");
  });
});

describe("field problems", () => {
  it("are tied to the input they are about", () => {
    const { rerender } = render(
      <Field label="Model" htmlFor="m" issue={{ level: "error", message: "Pick a model." }}>
        <Input id="m" />
      </Field>,
    );
    const input = screen.getByLabelText("Model");
    expect(input).toHaveAccessibleDescription("Pick a model.");
    expect(input).toHaveAttribute("aria-invalid", "true");
    rerender(
      <Field label="Model" htmlFor="m" issue={{ level: "warning", message: "Slow model." }}>
        <Input id="m" />
      </Field>,
    );
    expect(input).toHaveAccessibleDescription("Slow model.");
    expect(input).not.toHaveAttribute("aria-invalid");
    rerender(
      <Field label="Model" htmlFor="m">
        <Input id="m" />
      </Field>,
    );
    expect(input).not.toHaveAttribute("aria-describedby");
  });
});
