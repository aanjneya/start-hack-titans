import { connection } from "next/server";
import { Suspense } from "react";

async function RequestConnection() {
  await connection();
  return null;
}

// The workspace title depends on the URL. Leave the rest of the page available for prefetch.
export function WorkspaceMetadataMarker() {
  return <Suspense fallback={null}><RequestConnection /></Suspense>;
}
