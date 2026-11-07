import { proxyPost } from "../proxy";

export async function POST(request: Request) {
  return proxyPost("/score", request);
}
