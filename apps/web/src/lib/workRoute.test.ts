import { describe, expect, it } from "vitest";
import { parseWorkRoute, workRouteUrl } from "./workRoute";

describe("Work URL state", () => {
	it("round-trips a project chat session", () => {
		const route = parseWorkRoute({
			search: "?mode=work&view=chat&project=atlas&session=42",
			hash: "",
		});
		expect(route).toEqual({
			mode: "work",
			view: "chat",
			projectSlug: "atlas",
			sessionId: 42,
			workflowJobId: null,
			designId: null,
		});
		expect(
			workRouteUrl("http://localhost/", route),
		).toBe("/?mode=work&view=chat&project=atlas&session=42");
	});

	it("keeps deep Workflow and Design identity scoped to their surface", () => {
		const workflow = parseWorkRoute({
			search: "?mode=work&view=workflows&project=atlas&session=42&workflow=9",
			hash: "",
		});
		expect(workflow).toMatchObject({ sessionId: 42, workflowJobId: 9 });
		const design = parseWorkRoute({
			search:
				"?mode=work&view=design&project=atlas&session=42&design=launch-poster",
			hash: "",
		});
		expect(design).toMatchObject({
			sessionId: 42,
			designId: "launch-poster",
		});
		expect(workRouteUrl("http://localhost/", design)).toBe(
			"/?mode=work&view=design&project=atlas&session=42&design=launch-poster",
		);
		expect(
			parseWorkRoute({
				search: "?mode=work&view=chat&project=atlas&workflow=9&design=wrong",
				hash: "",
			}),
		).toMatchObject({ workflowJobId: null, designId: null });
	});

	it("drops Work context from Delegate and falls back stale views explicitly", () => {
		expect(
			parseWorkRoute({
				search: "?mode=delegate&view=design&project=atlas&session=42",
				hash: "",
			}),
		).toEqual({
			mode: "delegate",
			view: "master",
			projectSlug: null,
			sessionId: null,
			workflowJobId: null,
			designId: null,
		});
	});
});
