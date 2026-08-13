import fs from "fs-extra";
import { atomicWriteJson } from "./atomic.js";

const CLEAN = Object.freeze({ dbDirty: false, mediaDirty: false });

export function createDeliveryState(filePath) {
  let saveChain = Promise.resolve();
  return {
    async load() {
      try {
        const parsed = JSON.parse(await fs.readFile(filePath, "utf8"));
        if (!parsed || typeof parsed !== "object") throw new TypeError("invalid delivery state");
        return {
          dbDirty: parsed.dbDirty === true,
          mediaDirty: parsed.mediaDirty === true,
        };
      } catch (error) {
        if (error.code === "ENOENT") return { ...CLEAN };
        throw error;
      }
    },
    async save(state) {
      const snapshot = {
        dbDirty: state.dbDirty === true,
        mediaDirty: state.mediaDirty === true,
      };
      const next = saveChain.then(() => atomicWriteJson(filePath, snapshot, { mode: 0o600 }));
      saveChain = next.catch(() => {});
      await next;
    },
  };
}
