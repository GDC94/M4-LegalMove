import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw/http";
import { describe, expect, it } from "vitest";
import { MAX_IMAGE_BYTES } from "./analysis/upload-form-schema";
import { AnalysisScreen } from "./analysis-screen";
import { createApiClient } from "./api/client";
import type { AnalysisBody, ProgressEvent } from "./api/contract";
import {
  API,
  cachedDone,
  DONE_BODY,
  FAILED_BODY,
  finished,
  HUMAN_REVIEW_BODY,
  JOB_ID,
  KEY,
  pngFile,
  RUNNING_BODY,
  sseBody,
  sseFrames,
  stage,
} from "./test/fixtures";
import { server } from "./test/server";

const ALL_STAGES_DONE: ProgressEvent[] = [
  stage(1, "parse_original_contract", "started"),
  stage(2, "parse_original_contract", "done"),
  stage(3, "parse_amendment_contract", "started"),
  stage(4, "parse_amendment_contract", "done"),
  stage(5, "contextualization_agent", "started"),
  stage(6, "contextualization_agent", "done"),
  stage(7, "extraction_agent", "started"),
  stage(8, "extraction_agent", "done"),
  stage(9, "output_validation", "started"),
  stage(10, "output_validation", "done"),
];

function renderScreen() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const client = createApiClient({ baseUrl: API, apiKey: KEY, retryDelaysMs: [0, 0, 0] });
  render(
    <QueryClientProvider client={queryClient}>
      <AnalysisScreen client={client} pollIntervalMs={5} />
    </QueryClientProvider>,
  );
  return userEvent.setup({ applyAccept: false });
}

function fileInput(role: "Original contract" | "Amendment"): HTMLInputElement {
  return within(screen.getByRole("group", { name: role })).getByLabelText(
    "Upload attachments",
  ) as HTMLInputElement;
}

/** The keyboard control of one upload: the drop zone button. */
function dropZone(role: "Original contract" | "Amendment"): HTMLElement {
  return within(screen.getByRole("group", { name: role })).getByRole("button", {
    name: /Drop a PNG or JPEG/,
  });
}

type Posted = { original: string; amendment: string };

/** Serves POST, the event stream and the body; records what each request carried. */
function serveAnalysis(options: { events: ProgressEvent[]; body: AnalysisBody }) {
  const posted: Posted[] = [];
  server.use(
    http.post(`${API}/analyses`, async ({ request }) => {
      const form = await request.formData();
      posted.push({
        original: (form.get("original") as File).name,
        amendment: (form.get("amendment") as File).name,
      });
      return HttpResponse.json({ id: JOB_ID }, { status: 202 });
    }),
    http.get(`${API}/analyses/:id/events`, () => {
      return new HttpResponse(sseBody(sseFrames(...options.events)), {
        headers: { "content-type": "text/event-stream" },
      });
    }),
    http.get(`${API}/analyses/:id`, () => HttpResponse.json(options.body)),
  );
  return posted;
}

async function chooseOriginal(
  user: ReturnType<typeof userEvent.setup>,
  file = pngFile("contract.png"),
) {
  await user.upload(fileInput("Original contract"), file);
}

async function chooseAmendment(
  user: ReturnType<typeof userEvent.setup>,
  file = pngFile("addendum.png"),
) {
  await user.upload(await amendmentInput(), file);
}

async function amendmentInput(): Promise<HTMLInputElement> {
  const group = await screen.findByRole("group", { name: "Amendment" });
  return within(group).getByLabelText("Upload attachments") as HTMLInputElement;
}

async function submitPair(user: ReturnType<typeof userEvent.setup>) {
  await chooseOriginal(user);
  await chooseAmendment(user);
  await user.click(screen.getByRole("button", { name: "Analyze" }));
}

const PDF = () => new File(["%PDF-1.7"], "scan.pdf", { type: "application/pdf" });

describe("upload form", () => {
  it("starts with only the original contract upload and Analyze disabled", () => {
    renderScreen();

    expect(screen.getByRole("group", { name: "Original contract" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Amendment" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Analyze" })).toBeDisabled();
    expect(screen.getByRole("heading", { name: "Upload the original contract" })).toBeVisible();
  });

  it("marks the current step in a two-item list", async () => {
    const user = renderScreen();
    const steps = () => screen.getAllByRole("listitem").filter((item) => item.closest("ol"));
    expect(steps()).toHaveLength(2);
    expect(steps()[0]).toHaveAttribute("aria-current", "step");

    await chooseOriginal(user);

    await screen.findByRole("group", { name: "Amendment" });
    expect(steps()[0]).not.toHaveAttribute("aria-current");
    expect(steps()[1]).toHaveAttribute("aria-current", "step");
  });

  it("stays on step 1 with an alert and no file when the original is not an image", async () => {
    const user = renderScreen();

    await chooseOriginal(user, PDF());

    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a PNG or JPEG image.");
    expect(screen.getByRole("group", { name: "Original contract" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Amendment" })).not.toBeInTheDocument();
    expect(screen.queryByText("scan.pdf")).not.toBeInTheDocument();
  });

  it("advances on a valid image, focuses the step 2 title and announces the step", async () => {
    const user = renderScreen();
    const live = screen.getByRole("status");
    expect(live).toBeEmptyDOMElement();

    await chooseOriginal(user);

    const title = await screen.findByRole("heading", { name: "Upload the amendment" });
    await waitFor(() => expect(title).toHaveFocus());
    expect(title).toHaveAttribute("tabindex", "-1");
    expect(screen.getByRole("status")).toBe(live);
    expect(live).toHaveTextContent("Step 2 of 2: amendment");
    expect(screen.getByText("contract.png")).toBeInTheDocument();
  });

  it("rejects an invalid amendment in step 2 without leaving the step", async () => {
    const user = renderScreen();
    await chooseOriginal(user);

    await chooseAmendment(user, PDF());

    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a PNG or JPEG image.");
    expect(screen.getByRole("heading", { name: "Upload the amendment" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Analyze" })).toBeDisabled();
  });

  it("enables Analyze once both images are valid", async () => {
    const user = renderScreen();
    await chooseOriginal(user);
    await chooseAmendment(user);

    expect(screen.getByRole("button", { name: "Analyze" })).toBeEnabled();
  });

  it("lets the amendment be removed, which disables Analyze again", async () => {
    const user = renderScreen();
    await chooseOriginal(user);
    await chooseAmendment(user);
    const group = screen.getByRole("group", { name: "Amendment" });

    await user.click(
      await within(group).findByRole("button", { name: "Remove addendum.png" }, { timeout: 3000 }),
    );

    await waitFor(() => expect(screen.getByRole("button", { name: "Analyze" })).toBeDisabled());
  });

  it("Change goes back to step 1, keeps the amendment and focuses the step 1 title", async () => {
    const user = renderScreen();
    await chooseOriginal(user);
    await chooseAmendment(user);

    await user.click(screen.getByRole("button", { name: "Change" }));

    const title = await screen.findByRole("heading", { name: "Upload the original contract" });
    await waitFor(() => expect(title).toHaveFocus());
    expect(screen.getByRole("status")).toHaveTextContent("Step 1 of 2: original contract");
    expect(screen.getByText("addendum.png")).toBeInTheDocument();
    expect(screen.queryByText("contract.png")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Analyze" })).toBeDisabled();

    await chooseOriginal(user, pngFile("replacement.png"));

    await screen.findByRole("heading", { name: "Upload the amendment" });
    expect(screen.getByText("replacement.png")).toBeInTheDocument();
    expect(screen.getByText("addendum.png")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Analyze" })).toBeEnabled();
  });

  it("sends both images in the multipart under the role of their own upload", async () => {
    const posted = serveAnalysis({
      events: [...ALL_STAGES_DONE, finished(11, "done")],
      body: DONE_BODY,
    });
    const user = renderScreen();

    await submitPair(user);

    await screen.findByRole("heading", { name: "Legal changes" });
    expect(posted).toEqual([{ original: "contract.png", amendment: "addendum.png" }]);
  });

  it("sends the replaced original with the amendment kept from before", async () => {
    const posted = serveAnalysis({ events: [], body: DONE_BODY });
    const user = renderScreen();
    await chooseOriginal(user);
    await chooseAmendment(user);
    await user.click(screen.getByRole("button", { name: "Change" }));
    await chooseOriginal(user, pngFile("replacement.png"));

    await user.click(await screen.findByRole("button", { name: "Analyze" }));

    await waitFor(() =>
      expect(posted).toEqual([{ original: "replacement.png", amendment: "addendum.png" }]),
    );
  });

  it("moves focus to the progress heading once the form gives way to the stepper", async () => {
    serveAnalysis({ events: [...ALL_STAGES_DONE, finished(11, "done")], body: DONE_BODY });
    const user = renderScreen();

    await submitPair(user);

    expect(await screen.findByRole("heading", { name: "Analysis progress" })).toHaveFocus();
  });

  it("swaps the form for the stepper, then keeps the stepper above the result", async () => {
    serveAnalysis({ events: [...ALL_STAGES_DONE, finished(11, "done")], body: DONE_BODY });
    const user = renderScreen();

    await submitPair(user);

    const result = await screen.findByRole("region", { name: "Legal changes" });
    const progress = screen.getByRole("heading", { name: "Analysis progress" });
    expect(screen.queryByRole("button", { name: "Analyze" })).not.toBeInTheDocument();
    expect(
      progress.compareDocumentPosition(result) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
  });

  it("announces stage changes from a live region mounted before the stepper animates in", async () => {
    serveAnalysis({
      events: [
        stage(1, "parse_original_contract", "started"),
        cachedDone(2, "parse_original_contract"),
        stage(3, "parse_amendment_contract", "started"),
        cachedDone(4, "parse_amendment_contract"),
      ],
      body: RUNNING_BODY,
    });
    const user = renderScreen();
    // Present and empty while idle: an announcement is only read out when the region's text
    // changes, so a region that mounted with the first stage would swallow it.
    const live = screen.getByRole("status");
    expect(live).toHaveAttribute("aria-live", "polite");
    expect(live).toBeEmptyDOMElement();

    await submitPair(user);

    await within(live).findByText("Read the amendment: done (from cache)");
    expect(screen.getByRole("status")).toBe(live);
  });

  it("rejects a type that is not PNG or JPEG and an image over the limit", async () => {
    const user = renderScreen();

    await chooseOriginal(user, new File(["GIF89a"], "scan.gif", { type: "image/gif" }));
    expect(await screen.findByText("Choose a PNG or JPEG image.")).toHaveRole("alert");

    await chooseOriginal(user);
    await chooseAmendment(user, pngFile("huge.png", MAX_IMAGE_BYTES + 1));

    expect(await screen.findByText("huge.png is over 10 MB.")).toHaveRole("alert");
    expect(dropZone("Amendment")).toHaveAccessibleDescription("huge.png is over 10 MB.");
    expect(screen.getByRole("button", { name: "Analyze" })).toBeDisabled();
  });
});

describe("terminal views", () => {
  it("done: shows the stepper complete and the legal changes from the body", async () => {
    serveAnalysis({ events: [...ALL_STAGES_DONE, finished(11, "done")], body: DONE_BODY });
    const user = renderScreen();

    await submitPair(user);

    const result = await screen.findByRole("region", { name: "Legal changes" });
    expect(within(result).getAllByText("3").length).toBeGreaterThan(0);
    expect(within(result).getAllByText("new:5").length).toBeGreaterThan(0);
    expect(within(result).getByText("price_payment")).toBeInTheDocument();
    expect(within(result).getByRole("heading", { name: "Change details" })).toBeInTheDocument();
    expect(within(result).getAllByRole("article")).toHaveLength(2);
    expect(within(result).getByText("delta")).toBeInTheDocument();
    expect(
      within(result).getByText(DONE_BODY.output?.summary_of_the_change ?? ""),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Validate the result: done");
  });

  it("human_review: shows why a person must review it, issue by issue", async () => {
    serveAnalysis({
      events: [
        ...ALL_STAGES_DONE.slice(0, 7),
        stage(8, "extraction_agent", "failed"),
        finished(9, "human_review"),
      ],
      body: HUMAN_REVIEW_BODY,
    });
    const user = renderScreen();

    await submitPair(user);

    const review = await screen.findByRole("region", { name: "Needs human review" });
    expect(within(review).getByText("ungrounded_quote")).toBeInTheDocument();
    expect(
      within(review).getByText("the quote is not in the original contract"),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Extract the changes: failed");
  });

  it("failed: shows the message and starts over with an empty form", async () => {
    serveAnalysis({
      events: [
        stage(1, "parse_original_contract", "started"),
        stage(2, "parse_original_contract", "failed"),
        finished(3, "failed"),
      ],
      body: FAILED_BODY,
    });
    const user = renderScreen();

    await submitPair(user);
    const failure = await screen.findByRole("alert");
    expect(failure).toHaveTextContent("vision call failed");

    await user.click(within(failure).getByRole("button", { name: "Start over" }));
    expect(screen.getByRole("button", { name: "Analyze" })).toBeInTheDocument();
    expect(screen.queryByText("contract.png")).not.toBeInTheDocument();
  });

  it("shows a refusal of the upload as an error, not a half result", async () => {
    server.use(
      http.post(`${API}/analyses`, () =>
        HttpResponse.json({ detail: "the analysis queue is full; retry later" }, { status: 503 }),
      ),
    );
    const user = renderScreen();

    await submitPair(user);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "the analysis queue is full; retry later",
    );
  });

  it("treats an event outside the contract as a contract error", async () => {
    serveAnalysis({ events: [], body: DONE_BODY });
    server.use(
      http.get(
        `${API}/analyses/:id/events`,
        () =>
          new HttpResponse(sseBody('id: 1\ndata: {"seq":1,"kind":"stage","stage":"ocr"}\n\n'), {
            headers: { "content-type": "text/event-stream" },
          }),
      ),
    );
    const user = renderScreen();

    await submitPair(user);

    expect(await screen.findByRole("alert")).toHaveTextContent("outside its contract");
    expect(screen.queryByRole("region", { name: "Legal changes" })).not.toBeInTheDocument();
  });
});

describe("stream fallback", () => {
  it("polls GET /analyses/{id} until the end once the stream fails three reconnections", async () => {
    serveAnalysis({ events: [], body: DONE_BODY });
    let streamAttempts = 0;
    let polls = 0;
    server.use(
      http.get(`${API}/analyses/:id/events`, () => {
        streamAttempts += 1;
        return new HttpResponse(null, { status: 502 });
      }),
      http.get(`${API}/analyses/:id`, () => {
        polls += 1;
        return HttpResponse.json(polls < 3 ? RUNNING_BODY : DONE_BODY);
      }),
    );
    const user = renderScreen();

    await submitPair(user);

    expect(await screen.findByRole("region", { name: "Legal changes" })).toBeInTheDocument();
    expect(streamAttempts).toBe(4);
    expect(polls).toBe(3);
  });
});
