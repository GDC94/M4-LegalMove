import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw/http";
import { describe, expect, it } from "vitest";
import { MAX_IMAGE_BYTES } from "./analysis/upload-form-schema";
import { AnalysisScreen } from "./analysis-screen";
import { createApiClient } from "./api/client";
import type { AnalysisBody, ProgressEvent } from "./api/contract";
import {
  API,
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

async function submitPair(user: ReturnType<typeof userEvent.setup>) {
  await user.upload(fileInput("Original contract"), pngFile("contract.png"));
  await user.upload(fileInput("Amendment"), pngFile("addendum.png"));
  await user.click(screen.getByRole("button", { name: "Analyze" }));
}

describe("upload form", () => {
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

  it("does not send with a single image", async () => {
    const posted = serveAnalysis({ events: [], body: DONE_BODY });
    const user = renderScreen();

    await user.upload(fileInput("Original contract"), pngFile("contract.png"));
    await user.click(screen.getByRole("button", { name: "Analyze" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Choose the amendment image.");
    expect(dropZone("Amendment")).toHaveAccessibleDescription("Choose the amendment image.");
    expect(posted).toEqual([]);
  });

  it("moves focus to the progress heading once the form gives way to the stepper", async () => {
    serveAnalysis({ events: [...ALL_STAGES_DONE, finished(11, "done")], body: DONE_BODY });
    const user = renderScreen();

    await submitPair(user);

    expect(await screen.findByRole("heading", { name: "Analysis progress" })).toHaveFocus();
  });

  it("rejects a type that is not PNG or JPEG and an image over the limit", async () => {
    const posted = serveAnalysis({ events: [], body: DONE_BODY });
    const user = renderScreen();

    await user.upload(
      fileInput("Original contract"),
      new File(["GIF89a"], "scan.gif", { type: "image/gif" }),
    );
    await user.upload(fileInput("Amendment"), pngFile("huge.png", MAX_IMAGE_BYTES + 1));
    await user.click(screen.getByRole("button", { name: "Analyze" }));

    expect(await screen.findByText("Choose a PNG or JPEG image.")).toHaveRole("alert");
    expect(screen.getByText("huge.png is over 10 MB.")).toHaveRole("alert");
    // The GIF filled its upload, so the drop zone is disabled: the group carries the message.
    expect(screen.getByRole("group", { name: "Original contract" })).toHaveAccessibleDescription(
      "Choose a PNG or JPEG image.",
    );
    expect(dropZone("Amendment")).toHaveAccessibleDescription(
      "huge.png is over 10 MB. Choose the amendment image.",
    );
    expect(posted).toEqual([]);
  });
});

describe("terminal views", () => {
  it("done: shows the stepper complete and the legal changes from the body", async () => {
    serveAnalysis({ events: [...ALL_STAGES_DONE, finished(11, "done")], body: DONE_BODY });
    const user = renderScreen();

    await submitPair(user);

    const result = await screen.findByRole("region", { name: "Legal changes" });
    expect(within(result).getByText("3")).toBeInTheDocument();
    expect(within(result).getByText("new:5")).toBeInTheDocument();
    expect(within(result).getByText("price")).toBeInTheDocument();
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
