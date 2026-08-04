import { defineConfig } from "vite";

function localApiProxy() {
  return {
    "/api": {
      target: "http://127.0.0.1:3000",
      changeOrigin: false,
    },
  };
}

export default defineConfig(({ command }) => ({
  define:
    command === "serve"
      ? { "import.meta.env.VITE_API_BASE_URL": JSON.stringify("") }
      : undefined,
  server: { proxy: localApiProxy() },
  preview: { proxy: localApiProxy() },
}));
