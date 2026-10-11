import { readdir, readFile, writeFile } from "node:fs/promises";
import { brotliCompressSync, constants, gzipSync } from "node:zlib";
import { resolve } from "node:path";
const root = resolve(import.meta.dir, "../dist/chat");
for (const name of await readdir(root)) {
  if (!/\.(js|css|html)$/.test(name)) continue;
  const bytes = await readFile(resolve(root, name));
  await Promise.all([
    writeFile(resolve(root, `${name}.br`), brotliCompressSync(bytes, { params: { [constants.BROTLI_PARAM_QUALITY]: 6 } })),
    writeFile(resolve(root, `${name}.gz`), gzipSync(bytes, { level: 6 })),
  ]);
}
