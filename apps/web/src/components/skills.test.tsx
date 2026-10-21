import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../lib/api";
import type { Skill, SkillSummary } from "../lib/types";
import { SkillsField } from "./inspector/AgentFields";
import { Skills, withName } from "./Skills";
import { TooltipProvider } from "./ui";

vi.mock("../lib/api", async (original) => ({
  ...(await original<typeof import("../lib/api")>()),
  api: { skills: vi.fn(), skill: vi.fn(), saveSkill: vi.fn(), deleteSkill: vi.fn(), inbox: vi.fn(async () => []) },
}));

const mocked = api as unknown as Record<"skills" | "skill" | "saveSkill" | "deleteSkill", ReturnType<typeof vi.fn>>;

const builtIn: SkillSummary = {
  name: "research-report",
  description: "How to research a topic and write a short report.",
  files: ["SKILL.md", "outline.md"],
  built_in: true,
  replaces_built_in: false,
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the Skills library", () => {
  it("keeps the name in SKILL.md the same as the skill's", () => {
    const md = "---\nname: old\ndescription: d\n---\n\nname: in the body stays";
    expect(withName(md, "new-name")).toBe("---\nname: new-name\ndescription: d\n---\n\nname: in the body stays");
  });

  it("creates a skill, and says what to fix when it can't be saved", async () => {
    mocked.skills.mockResolvedValue([builtIn]);
    mocked.saveSkill.mockRejectedValueOnce(new ApiError(422, "SKILL.md needs a description."));
    mocked.saveSkill.mockResolvedValueOnce({ ...builtIn, name: "tone-of-voice", built_in: false });
    render(
      <TooltipProvider>
        <Skills name="new" />
      </TooltipProvider>,
    );
    expect(await screen.findByText("research-report")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Tone of voice" } });
    const body = screen.getByLabelText("Contents of SKILL.md") as HTMLTextAreaElement;
    expect(body.value).toContain("name: tone-of-voice");
    fireEvent.click(screen.getByTestId("save-skill"));
    expect(await screen.findByText("SKILL.md needs a description.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("New file name"), { target: { value: "examples/good.md" } });
    fireEvent.click(screen.getByText("Add a file"));
    fireEvent.change(screen.getByLabelText("Contents of examples/good.md"), { target: { value: "Short." } });
    fireEvent.click(screen.getByTestId("save-skill"));
    await waitFor(() => expect(mocked.saveSkill).toHaveBeenCalledTimes(2));
    const [name, files] = mocked.saveSkill.mock.calls[1];
    expect(name).toBe("tone-of-voice");
    expect(files["examples/good.md"]).toBe("Short.");
    expect(files["SKILL.md"]).toContain("name: tone-of-voice");
  });

  it("offers to save a copy of a built-in skill", async () => {
    const full: Skill = { ...builtIn, files: { "SKILL.md": "---\nname: research-report\ndescription: d\n---\n", "outline.md": "# Title" } };
    mocked.skills.mockResolvedValue([builtIn]);
    mocked.skill.mockResolvedValue(full);
    render(
      <TooltipProvider>
        <Skills name="research-report" />
      </TooltipProvider>,
    );
    expect(await screen.findByText("Save my copy")).toBeTruthy();
    expect(screen.queryByText("Delete")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "outline.md" }));
    expect((screen.getByLabelText("Contents of outline.md") as HTMLTextAreaElement).value).toBe("# Title");
  });
});

describe("a step's skills", () => {
  it("picks skills from the library and flags one that is gone", async () => {
    mocked.skills.mockResolvedValue([builtIn]);
    const onChange = vi.fn();
    render(
      <SkillsField
        field={{ key: "skills", label: "Skills", kind: "skills" }}
        value={["gone"]}
        onChange={onChange}
        settings={{}}
        fields={[]}
        id="skills"
      />,
    );
    fireEvent.click(await screen.findByRole("checkbox"));
    expect(onChange).toHaveBeenCalledWith(["gone", "research-report"]);
    expect(screen.getByText("“gone” isn't in the Skills library.")).toBeTruthy();
    fireEvent.click(screen.getByText("Take it off"));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });
});
