// @ts-check
import { defineConfig } from "astro/config";
import optersoft from "@optersoft/astro/integration";

// make.optersoft.com: one page and a 404, static, on Cloudflare Pages (project
// `mkrun`). The chrome — layout, header, footer, theme, brand, fonts — is
// `@optersoft/astro`, the sibling checkout at ../../astro; its integration
// registers Tailwind and injects src/styles/global.css into every page.
export default defineConfig({
  site: "https://make.optersoft.com",
  trailingSlash: "never",
  build: { format: "file" },
  integrations: [optersoft()],
});
