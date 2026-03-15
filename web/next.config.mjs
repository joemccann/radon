import { dirname, resolve } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));

const config = {
  outputFileTracingRoot: resolve(__dirname, ".."),
  turbopack: {},
  webpack: (config) => {
    config.resolve.alias["@tools"] = resolve(__dirname, "..", "lib", "tools");
    return config;
  },
};

export default config;
