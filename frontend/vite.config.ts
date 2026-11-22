import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Geliştirme sırasında /api istekleri arka uca (localhost:8000) yönlendirilir.
// Böylece tarayıcı tek adresle çalışır ve CORS ayarı gerekmez.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
});
