import { spawn } from "node:child_process";

const FRAME_WIDTH = 640;
const TIMEOUT_MS = 8000;

/**
 * Grab one JPEG from a remote clip. Used when the browser can't read the <video> pixels
 * (TfL JamCams are served from S3 without CORS headers, so the canvas is tainted).
 * debt: shells out to ffmpeg. ceiling: only hosts with ffmpeg on PATH. upgrade: proxy the
 * clip through a same-origin route and capture in the browser when deploying off the demo Mac.
 */
export function grabFrame(videoUrl: string): Promise<Buffer> {
  const url = new URL(videoUrl);
  if (url.protocol !== "https:") throw new Error("videoUrl must be https");

  return new Promise((resolve, reject) => {
    const args = [
      "-loglevel", "error",
      "-ss", "1",
      "-i", url.href,
      "-frames:v", "1",
      "-vf", `scale=${FRAME_WIDTH}:-2`,
      "-q:v", "6",
      "-f", "image2",
      "pipe:1",
    ];
    const ff = spawn("ffmpeg", args, { stdio: ["ignore", "pipe", "pipe"] });
    const chunks: Buffer[] = [];
    let stderr = "";
    const timer = setTimeout(() => ff.kill("SIGKILL"), TIMEOUT_MS);

    ff.stdout.on("data", (c: Buffer) => chunks.push(c));
    ff.stderr.on("data", (c: Buffer) => (stderr += c.toString()));
    ff.on("error", (err) => {
      clearTimeout(timer);
      reject(new Error(`ffmpeg unavailable: ${err.message}`));
    });
    ff.on("close", (code) => {
      clearTimeout(timer);
      const out = Buffer.concat(chunks);
      if (code === 0 && out.length > 0) resolve(out);
      else reject(new Error(`ffmpeg exited ${code}: ${stderr.trim().slice(0, 200)}`));
    });
  });
}
