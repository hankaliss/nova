import { describe, expect, it } from "vitest";
import { displayTitle, extractArtifacts, hideToolArtifacts, indexArtifacts } from "./artifacts";

const page = (title: string, body = "<p>x</p>") =>
  "```html\n<!DOCTYPE html><html><head><title>" + title + "</title></head><body>" + body + "</body></html>\n```";

describe("extractArtifacts", () => {
  it("finds complete html documents and their titles", () => {
    const arts = extractArtifacts("Here:\n\n" + page("Counter"), 3);
    expect(arts).toHaveLength(1);
    expect(arts[0]).toMatchObject({ id: "art-3-0", title: "Counter", type: "html" });
  });

  it("ignores snippets that aren't a page", () => {
    expect(extractArtifacts("```html\n<b>hi</b>\n```", 0)).toEqual([]);
    expect(extractArtifacts("no code", 0)).toEqual([]);
  });
});

describe("indexArtifacts", () => {
  const messages = [
    { role: "user", content: "make a counter" },
    { role: "assistant", content: page("Counter v1") },
    { role: "user", content: "and a clock" },
    { role: "assistant", content: page("Clock") },
    { role: "user", content: "make the counter red" },
    { role: "assistant", content: page("Counter v2", "<p style='color:red'>x</p>") },
  ];

  it("numbers revisions of a title across the conversation", () => {
    const index = indexArtifacts(messages);
    const counter = index.groups.get("counter")!;
    expect(counter.map((a) => [a.id, a.version, a.versions])).toEqual([
      ["art-1-0", 1, 2],
      ["art-5-0", 2, 2],
    ]);
    expect(index.groups.get("clock")![0].versions).toBe(1);
    expect(index.byMessage.get(3)![0].title).toBe("Clock");
    expect(index.byId.get("art-5-0")!.content).toContain("color:red");
  });

  it("drops a vN suffix from the title only when there are revisions", () => {
    const index = indexArtifacts(messages);
    expect(displayTitle(index.byId.get("art-1-0")!)).toBe("Counter");
    const single = indexArtifacts([{ role: "assistant", content: page("Report v3") }]);
    expect(displayTitle(single.byId.get("art-0-0")!)).toBe("Report v3");
  });

  it("skips user messages", () => {
    expect(indexArtifacts([{ role: "user", content: page("Mine") }]).byId.size).toBe(0);
  });
});

describe("tool artifacts (AD-039)", () => {
  const report =
    "\n\n```html\n<!-- nova:artifact -->\n<!DOCTYPE html><html><head><title>System doctor</title></head><body><p>x</p></body></html>\n```\n\n";

  it("are flagged and still extracted as artifacts", () => {
    const arts = extractArtifacts("Found:" + report + "Two units failed.", 1);
    expect(arts).toHaveLength(1);
    expect(arts[0]).toMatchObject({ title: "System doctor", tool: true });
    expect(extractArtifacts(page("Mine"), 1)[0].tool).toBe(false);
  });

  it("hide their code in the chat, also while still streaming", () => {
    expect(hideToolArtifacts("Found:" + report + "Two units failed.")).toBe("Found:\n\nTwo units failed.");
    const partial = "Found:\n\n```html\n<!-- nova:artifact -->\n<!DOCTYPE html><html><he";
    expect(hideToolArtifacts(partial)).toBe("Found:\n\n");
  });

  it("leave model-written html alone", () => {
    expect(hideToolArtifacts(page("Mine"))).toBe(page("Mine"));
  });
});
