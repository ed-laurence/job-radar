import { proxyOptionsFromEnv, proxyToApi } from "@/lib/api/bff";

// Public liveness for uptime checks: proxied to the private api's /healthz so a healthy
// result means web *and* api are up. Forwards no client headers; body stays {"status":"ok"}.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToApi(request, proxyOptionsFromEnv(process.env, []));
}
