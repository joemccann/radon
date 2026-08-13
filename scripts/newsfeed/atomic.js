import path from "node:path";
import { copyFile, open, rename, stat, unlink } from "node:fs/promises";
import fs from "fs-extra";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function syncDirectory(directory) {
  let handle;
  try {
    handle = await open(directory, "r");
    await handle.sync();
  } catch {
    // Some filesystems do not allow directory fsync. The file itself was
    // already flushed before rename, so keep the portability fallback.
  } finally {
    await handle?.close().catch(() => {});
  }
}

export async function atomicWriteText(
  filePath,
  contents,
  { mode, backupPath = null } = {},
) {
  const directory = path.dirname(filePath);
  await fs.ensureDir(directory);
  let existingMode = mode;
  try {
    existingMode ??= (await stat(filePath)).mode & 0o777;
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  existingMode ??= 0o600;

  const temporary = path.join(
    directory,
    `.${path.basename(filePath)}.${process.pid}.${Date.now()}.${Math.random().toString(16).slice(2)}.tmp`,
  );
  let handle;
  try {
    handle = await open(temporary, "wx", existingMode);
    await handle.writeFile(contents, "utf8");
    await handle.sync();
    await handle.close();
    handle = null;

    if (backupPath) {
      try {
        await copyFile(filePath, backupPath);
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
    }

    await rename(temporary, filePath);
    await fs.chmod(filePath, existingMode);
    await syncDirectory(directory);
  } catch (error) {
    await handle?.close().catch(() => {});
    await unlink(temporary).catch(() => {});
    throw error;
  }
}

export async function atomicWriteJson(filePath, value, options = {}) {
  const contents = `${JSON.stringify(value, null, 2)}\n`;
  JSON.parse(contents);
  await atomicWriteText(filePath, contents, options);
}

export async function withFileLock(
  targetPath,
  operation,
  { timeoutMs = 5_000, staleMs = 30_000 } = {},
) {
  const lockPath = `${targetPath}.lock`;
  const deadline = Date.now() + timeoutMs;
  await fs.ensureDir(path.dirname(lockPath));
  let lock;

  while (!lock) {
    try {
      lock = await open(lockPath, "wx", 0o600);
      await lock.writeFile(`${process.pid}\n`, "utf8");
      await lock.sync();
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
      try {
        const info = await stat(lockPath);
        if (Date.now() - info.mtimeMs > staleMs) {
          await unlink(lockPath);
          continue;
        }
      } catch (statError) {
        if (statError.code !== "ENOENT") throw statError;
        continue;
      }
      if (Date.now() >= deadline) throw new Error(`timed out acquiring lock for ${targetPath}`);
      await sleep(10 + Math.floor(Math.random() * 15));
    }
  }

  try {
    return await operation();
  } finally {
    await lock.close().catch(() => {});
    await unlink(lockPath).catch(() => {});
  }
}
