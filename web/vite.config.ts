import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

const DEFAULT_API_TARGET = "http://127.0.0.1:8000";

export default defineConfig(({ mode }) => {
  // The dev server forwards `/api` to uvicorn, so the browser stays same-origin and the API
  // needs no CORS. `VITE_API_PROXY_TARGET` (shell or `.env.local`) moves the target.
  const env = loadEnv(mode, process.cwd(), "VITE_");
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
    },
    // Split the heavy vendors out of the app chunk so no chunk passes Vite's 500 kB warning;
    // `motion` alone is most of the bundle and changes far less often than the app.
    build: {
      rolldownOptions: {
        output: {
          codeSplitting: {
            groups: [
              {
                name: "motion",
                test: /node_modules[\\/](motion|motion-dom|motion-utils|framer-motion)[\\/]/,
                priority: 20,
              },
              {
                name: "react",
                test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/,
                priority: 15,
              },
              { name: "vendor", test: /node_modules[\\/]/, priority: 10 },
            ],
          },
        },
      },
    },
    server: {
      proxy: {
        "/api": {
          target: env.VITE_API_PROXY_TARGET ?? DEFAULT_API_TARGET,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ""),
        },
      },
    },
    test: {
      environment: "happy-dom",
      setupFiles: ["./src/test/setup.ts"],
      restoreMocks: true,
    },
  };
});
