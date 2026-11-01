import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { AnalysisScreen } from "./analysis-screen";
import { createApiClient } from "./api/client";
import "./globals.css";

// Same origin only: the API sends no CORS headers and `X-API-Key` forces a preflight, so the
// front always goes through the `/api` proxy rather than calling uvicorn directly.
const client = createApiClient({
  baseUrl: new URL("/api/", window.location.origin).toString(),
  apiKey: import.meta.env.VITE_API_KEY ?? "",
});
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root element");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AnalysisScreen client={client} />
    </QueryClientProvider>
  </StrictMode>,
);
