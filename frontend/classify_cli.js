// Tiny bridge used only by tests/test_frontend_against_backend.py: reads
// {"httpStatus": <int>, "body": <object>} as JSON from stdin, runs the
// real frontend classifyUploadResponse() on it, and prints the result as
// JSON on stdout. Lets a Python test drive *real* FastAPI responses
// through the *actual* frontend logic (not a reimplementation of it)
// without needing a browser.
const path = require("node:path");
const { classifyUploadResponse } = require(
  path.join(__dirname, "upload_flow.js")
);

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => (raw += chunk));
process.stdin.on("end", () => {
  const input = JSON.parse(raw);
  const result = classifyUploadResponse(input.httpStatus, input.body);
  process.stdout.write(JSON.stringify(result));
});
