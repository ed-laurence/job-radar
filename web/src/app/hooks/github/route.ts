import { GITHUB_HOOK_REQUEST_HEADERS, proxyOptionsFromEnv, proxyToApi } from "@/lib/api/bff";

// BFF: GitHub webhooks. The raw body is forwarded byte for byte; the api verifies the HMAC.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function POST(request: Request): Promise<Response> {
  return proxyToApi(request, proxyOptionsFromEnv(process.env, GITHUB_HOOK_REQUEST_HEADERS));
}
