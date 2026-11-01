import type { components } from "@cgl/contracts";
import { api, ApiError, send, type Me } from "./workspace-api";
type Upload = components["schemas"]["UploadResponse"];
const hash = async (bytes: BufferSource) =>
  Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
function remembered(key: string) {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}
function remember(key: string, value: string | null) {
  try {
    if (value) sessionStorage.setItem(key, value);
    else sessionStorage.removeItem(key);
  } catch {
    /* Resume remains available during this page session. */
  }
}
export async function uploadDocument(
  file: File,
  submission: string,
  me: Me,
  replaces: string,
  onProgress: (text: string) => void,
) {
  if (!/\.(pdf|xlsx)$/i.test(file.name) || file.size < 1 || file.size > 50 * 1024 * 1024)
    throw new Error("Choose a PDF or XLSX between 1 byte and 50 MB.");
  onProgress("Checking document bytes…");
  const sha256 = await hash(await file.arrayBuffer());
  const key =
    "cgl-upload:" +
    (await hash(
      new TextEncoder().encode(
        JSON.stringify([
          me.user_id,
          me.tenant_id,
          submission,
          replaces,
          sha256,
          file.name,
        ]),
      ),
    ));
  let upload: Upload | undefined;
  const previous = remembered(key);
  if (previous)
    try {
      upload = await api<Upload>("/uploads/" + previous);
    } catch (error) {
      if (error instanceof ApiError && [404, 410].includes(error.status))
        remember(key, null);
      else throw error;
    }
  if (!upload) {
    upload = await send<Upload>("/submissions/" + submission + "/uploads", {
      filename: file.name,
      sha256,
      byte_size: file.size,
      supersedes_id: replaces || null,
    });
    remember(key, upload.id);
  }
  if (upload.document_id) {
    remember(key, null);
    onProgress("Document already received.");
    return;
  }
  const total = Math.ceil(file.size / upload.chunk_size);
  for (let number = 0; number < total; number++) {
    onProgress(
      `Uploading part ${number + 1} of ${total}. If interrupted, select this file again to resume.`,
    );
    if (upload.uploaded_parts.includes(number)) continue;
    await api<Upload>(`/uploads/${upload.id}/parts/${number}`, {
      method: "PUT",
      body: file.slice(number * upload.chunk_size, (number + 1) * upload.chunk_size),
      headers: {
        "Content-Type": "application/octet-stream",
        "Idempotency-Key": crypto.randomUUID(),
      },
    });
  }
  onProgress("Verifying the complete file and queuing safety checks…");
  await send<Upload>(`/uploads/${upload.id}/finalize`, {});
  remember(key, null);
  onProgress("Upload complete. Safety checks and document processing are queued.");
}
