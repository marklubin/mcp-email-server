/**
 * MCP Proxy Worker
 *
 * Handles GitHub OAuth authentication for Claude and proxies MCP requests
 * to the backend tunnel. Only allows access for authorized GitHub users.
 */

interface Env {
  BACKEND_URL: string;
  MCP_SECRET: string;
  GITHUB_CLIENT_ID: string;
  GITHUB_CLIENT_SECRET: string;
  ALLOWED_USERS: string; // Comma-separated GitHub usernames
}

// In-memory stores (use KV in production for persistence across instances)
const tokens = new Map<string, { userId: string; expiresAt: number }>();
const pendingAuth = new Map<string, { redirectUri: string; state: string; codeChallenge?: string }>();

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

    // OAuth authorize endpoint - redirects to GitHub
    if (path === "/authorize") {
      return handleAuthorize(request, url, env);
    }

    // GitHub callback - exchanges code and validates user
    if (path === "/callback") {
      return handleGitHubCallback(request, url, env);
    }

    // OAuth token endpoint
    if (path === "/token") {
      return handleToken(request, env);
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

function handleAuthorize(request: Request, url: URL, env: Env): Response {
  const redirectUri = url.searchParams.get("redirect_uri");
  const state = url.searchParams.get("state");
  const codeChallenge = url.searchParams.get("code_challenge");

  if (!redirectUri || !state) {
    return new Response("Missing required parameters", { status: 400 });
  }

  // Generate a unique ID to track this auth flow
  const authId = crypto.randomUUID();

  // Store the original redirect info
  pendingAuth.set(authId, {
    redirectUri,
    state,
    codeChallenge,
  });

  // Build GitHub OAuth URL
  const githubUrl = new URL("https://github.com/login/oauth/authorize");
  githubUrl.searchParams.set("client_id", env.GITHUB_CLIENT_ID);
  githubUrl.searchParams.set("redirect_uri", `${url.protocol}//${url.host}/callback`);
  githubUrl.searchParams.set("state", authId); // Use authId to correlate
  githubUrl.searchParams.set("scope", "read:user");

  return Response.redirect(githubUrl.toString(), 302);
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

async function handleGitHubCallback(request: Request, url: URL, env: Env): Promise<Response> {
  const code = url.searchParams.get("code");
  const authId = url.searchParams.get("state");

  if (!code || !authId) {
    return new Response("Missing code or state", { status: 400 });
  }

  // Retrieve the pending auth request
  const pending = pendingAuth.get(authId);
  if (!pending) {
    return new Response("Invalid or expired auth session", { status: 400 });
  }
  pendingAuth.delete(authId);

  // Exchange code for GitHub access token
  const tokenResponse = await fetch("https://github.com/login/oauth/access_token", {
    method: "POST",
    headers: {
      "Accept": "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      client_id: env.GITHUB_CLIENT_ID,
      client_secret: env.GITHUB_CLIENT_SECRET,
      code,
    }),
  });

  const tokenData = await tokenResponse.json() as { access_token?: string; error?: string };
  if (!tokenData.access_token) {
    return new Response(`GitHub OAuth failed: ${tokenData.error || "unknown error"}`, { status: 400 });
  }

  // Get GitHub user info
  const userResponse = await fetch("https://api.github.com/user", {
    headers: {
      "Authorization": `Bearer ${tokenData.access_token}`,
      "User-Agent": "MCP-Proxy-Worker",
    },
  });

  const userData = await userResponse.json() as { login?: string };
  if (!userData.login) {
    return new Response("Failed to get GitHub user info", { status: 400 });
  }

  // Check if user is allowed
  const allowedUsers = env.ALLOWED_USERS.split(",").map(u => u.trim().toLowerCase());
  if (!allowedUsers.includes(userData.login.toLowerCase())) {
    return new Response(
      `Access denied. User '${userData.login}' is not authorized.`,
      { status: 403 }
    );
  }

  // User is authorized - generate our own auth code
  const ourCode = crypto.randomUUID();
  tokens.set(`code:${ourCode}`, {
    userId: userData.login,
    expiresAt: Date.now() + 600000, // 10 minutes
  });

  // Redirect back to Claude with our code
  const redirectUrl = new URL(pending.redirectUri);
  redirectUrl.searchParams.set("code", ourCode);
  redirectUrl.searchParams.set("state", pending.state);

  return Response.redirect(redirectUrl.toString(), 302);
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
