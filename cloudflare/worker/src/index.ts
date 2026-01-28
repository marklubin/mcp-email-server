/**
 * MCP Proxy Worker
 *
 * Handles OAuth authentication for Claude and proxies MCP requests
 * to the backend tunnel with shared secret authentication.
 */

interface Env {
  BACKEND_URL: string;
  MCP_SECRET: string;
  OAUTH_CLIENT_SECRET?: string;
  // KV namespace for storing OAuth tokens/sessions (optional)
  // SESSIONS?: KVNamespace;
}

// In-memory token store (for demo - use KV in production)
const tokens = new Map<string, { userId: string; expiresAt: number }>();

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

    // CORS preflight
    if (request.method === "OPTIONS") {
      return handleCORS();
    }

    // OAuth metadata endpoint
    if (path === "/.well-known/oauth-authorization-server") {
      return handleOAuthMetadata(url);
    }

    // OAuth authorize endpoint
    if (path === "/authorize") {
      return handleAuthorize(request, url);
    }

    // OAuth token endpoint
    if (path === "/token") {
      return handleToken(request, env);
    }

    // OAuth callback (for completing the flow)
    if (path === "/callback") {
      return handleCallback(request, url);
    }

    // MCP endpoints - require auth and proxy to backend
    if (path === "/mcp" || path.startsWith("/mcp/")) {
      return handleMCPProxy(request, env, url);
    }

    // Health check
    if (path === "/health") {
      return new Response(JSON.stringify({ status: "ok", worker: "mcp-proxy" }), {
        headers: { "Content-Type": "application/json" },
      });
    }

    return new Response("Not Found", { status: 404 });
  },
};

function handleCORS(): Response {
  return new Response(null, {
    status: 204,
    headers: {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, Authorization, X-MCP-Secret",
      "Access-Control-Max-Age": "86400",
    },
  });
}

function handleOAuthMetadata(url: URL): Response {
  const baseUrl = `${url.protocol}//${url.host}`;
  const metadata = {
    issuer: baseUrl,
    authorization_endpoint: `${baseUrl}/authorize`,
    token_endpoint: `${baseUrl}/token`,
    response_types_supported: ["code"],
    grant_types_supported: ["authorization_code", "refresh_token"],
    code_challenge_methods_supported: ["S256"],
    token_endpoint_auth_methods_supported: ["client_secret_post", "client_secret_basic"],
  };

  return new Response(JSON.stringify(metadata), {
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
    },
  });
}

function handleAuthorize(request: Request, url: URL): Response {
  const clientId = url.searchParams.get("client_id");
  const redirectUri = url.searchParams.get("redirect_uri");
  const state = url.searchParams.get("state");
  const codeChallenge = url.searchParams.get("code_challenge");
  const codeChallengeMethod = url.searchParams.get("code_challenge_method");

  if (!clientId || !redirectUri || !state) {
    return new Response("Missing required parameters", { status: 400 });
  }

  // For now, auto-approve (in production, show consent screen)
  // Generate an authorization code
  const code = crypto.randomUUID();

  // Store code with metadata (in production, use KV)
  const codeData = {
    clientId,
    redirectUri,
    codeChallenge,
    codeChallengeMethod,
    createdAt: Date.now(),
  };
  // Store in memory (demo only - use KV in production)
  tokens.set(`code:${code}`, { userId: "demo-user", expiresAt: Date.now() + 600000 });

  // Redirect back to client with code
  const redirectUrl = new URL(redirectUri);
  redirectUrl.searchParams.set("code", code);
  redirectUrl.searchParams.set("state", state);

  return Response.redirect(redirectUrl.toString(), 302);
}

async function handleToken(request: Request, env: Env): Promise<Response> {
  if (request.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }

  const contentType = request.headers.get("Content-Type") || "";
  let params: URLSearchParams;

  if (contentType.includes("application/json")) {
    const body = await request.json() as Record<string, string>;
    params = new URLSearchParams(body);
  } else {
    params = new URLSearchParams(await request.text());
  }

  const grantType = params.get("grant_type");
  const code = params.get("code");
  const clientId = params.get("client_id");
  const clientSecret = params.get("client_secret");

  if (grantType === "authorization_code" && code) {
    // Validate code exists (in production, verify against stored data)
    const codeData = tokens.get(`code:${code}`);
    if (!codeData || codeData.expiresAt < Date.now()) {
      return new Response(JSON.stringify({ error: "invalid_grant" }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      });
    }

    // Clean up used code
    tokens.delete(`code:${code}`);

    // Generate access token
    const accessToken = crypto.randomUUID();
    const refreshToken = crypto.randomUUID();
    const expiresIn = 3600;

    // Store token (in production, use KV)
    tokens.set(`token:${accessToken}`, {
      userId: codeData.userId,
      expiresAt: Date.now() + expiresIn * 1000,
    });

    return new Response(
      JSON.stringify({
        access_token: accessToken,
        token_type: "Bearer",
        expires_in: expiresIn,
        refresh_token: refreshToken,
      }),
      {
        headers: {
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*",
        },
      }
    );
  }

  return new Response(JSON.stringify({ error: "unsupported_grant_type" }), {
    status: 400,
    headers: { "Content-Type": "application/json" },
  });
}

function handleCallback(request: Request, url: URL): Response {
  // This would be used if we had an external identity provider
  // For now, just show the code for debugging
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");

  return new Response(
    `Authorization complete. Code: ${code}, State: ${state}`,
    { headers: { "Content-Type": "text/plain" } }
  );
}

async function handleMCPProxy(request: Request, env: Env, url: URL): Promise<Response> {
  // Check for Bearer token
  const authHeader = request.headers.get("Authorization");
  if (!authHeader?.startsWith("Bearer ")) {
    return new Response(
      JSON.stringify({
        jsonrpc: "2.0",
        id: "auth-error",
        error: { code: -32001, message: "Missing or invalid Authorization header" },
      }),
      {
        status: 401,
        headers: {
          "Content-Type": "application/json",
          "WWW-Authenticate": 'Bearer realm="mcp"',
        },
      }
    );
  }

  const token = authHeader.slice(7);
  const tokenData = tokens.get(`token:${token}`);

  if (!tokenData || tokenData.expiresAt < Date.now()) {
    return new Response(
      JSON.stringify({
        jsonrpc: "2.0",
        id: "auth-error",
        error: { code: -32001, message: "Invalid or expired token" },
      }),
      {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }
    );
  }

  // Proxy to backend with shared secret
  const backendUrl = new URL(url.pathname + url.search, env.BACKEND_URL);

  const headers = new Headers(request.headers);
  headers.set("X-MCP-Secret", env.MCP_SECRET);
  headers.delete("Authorization"); // Don't forward OAuth token to backend

  const backendRequest = new Request(backendUrl.toString(), {
    method: request.method,
    headers,
    body: request.body,
    // @ts-ignore - duplex is needed for streaming
    duplex: "half",
  });

  const response = await fetch(backendRequest);

  // Forward response with CORS headers
  const responseHeaders = new Headers(response.headers);
  responseHeaders.set("Access-Control-Allow-Origin", "*");

  return new Response(response.body, {
    status: response.status,
    headers: responseHeaders,
  });
}
