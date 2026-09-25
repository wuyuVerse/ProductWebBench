const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const {Transform} = require("stream");
const {pipeline} = require("stream/promises");

async function captureDownload(page, action, directory) {
  if (!directory || !/^[A-Za-z0-9_-]+$/.test(action.artifact_id || "")) {
    throw new Error("download needs an artifact directory and safe artifact_id");
  }
  const limit = action.max_bytes === undefined ? 64 * 1024 * 1024 : action.max_bytes;
  if (!Number.isInteger(limit) || limit < 1 || limit > 64 * 1024 * 1024) throw new Error("invalid download size limit");
  const file = `download_${action.artifact_id}.bin`;
  const output = path.join(directory, file);
  if (fs.existsSync(output)) throw new Error("download artifact already exists");
  const target = page.locator(action.selector);
  if (await target.count() !== 1) throw new Error("download trigger must be unique");
  const [download] = await Promise.all([
    page.waitForEvent("download", {timeout: action.timeout_ms || 10000}),
    target.click({timeout: action.timeout_ms || 10000}),
  ]);
  let bytes = 0;
  const hash = crypto.createHash("sha256");
  let created = false;
  try {
    const stream = await download.createReadStream();
    if (!stream) throw new Error("download stream unavailable");
    const meter = new Transform({transform(chunk, encoding, done) {
      bytes += chunk.length;
      if (bytes > limit) return done(new Error("download exceeds size limit"));
      hash.update(chunk); done(null, chunk);
    }});
    const fd = fs.openSync(output, "wx");
    created = true;
    await pipeline(stream, meter, fs.createWriteStream(output, {fd}));
    if (!bytes) throw new Error("empty download");
    return {file, bytes, sha256: hash.digest("hex"), suggested_filename: download.suggestedFilename(),
            artifact_id: action.artifact_id, validation: "captured_bytes_only"};
  } catch (error) {
    if (created) fs.rmSync(output, {force:true});
    throw error;
  } finally {
    await download.delete();
  }
}

module.exports = {captureDownload};
