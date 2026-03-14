import { dirname, resolve } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));

const config = {
  outputFileTracingRoot: resolve(__dirname, ".."),
  compiler: {
    removeConsole: { exclude: ["error", "warn"] },
    reactRemoveProperties: { properties: ["^data-testid$"] },
  },
  experimental: {
    turbopackInferModuleSideEffects: true,
    inlineCss: true,
  },
  turbopack: {},
  webpack: (config) => {
    config.resolve.alias["@tools"] = resolve(__dirname, "..", "lib", "tools");
    return config;
  },
};

export default config;
