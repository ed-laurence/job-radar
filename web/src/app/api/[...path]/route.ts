import { API_REQUEST_HEADERS, proxyOptionsFromEnv, proxyToApi } from "@/lib/api/bff";

// BFF: same-origin /api/* to the private api service, streamed (SSE passes through unbuffered).
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function handle(request: Request): Promise<Response> {
  return proxyToApi(request, proxyOptionsFromEnv(process.env, API_REQUEST_HEADERS));
}

export { handle as DELETE, handle as GET, handle as PATCH, handle as POST, handle as PUT };
