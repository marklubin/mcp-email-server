import OAuthProvider, { type AuthProps } from "@cloudflare/workers-oauth-provider";
import { GitHubHandler } from "./github-handler";

// Context from the auth process, encrypted & stored in the auth token
type Props = {
	login: string;
	name: string;
	email: string;
	accessToken: string;
};

/**
 * MCP Proxy Handler
 *
 * After OAuth authentication, proxies MCP requests to the backend
 * server via Workers VPC (Cloudflare Tunnel). No public DNS exposure.
 */
async function handleMCPProxy(request: Request, env: Env, authProps?: AuthProps<Props>): Promise<Response> {
	const url = new URL(request.url);

	// Build request to private backend via VPC
	// The hostname is arbitrary - VPC routes based on the binding
	const backendUrl = `http://mcp-router.internal${url.pathname}${url.search}`;

	console.log(`[MCP Proxy] ${request.method} ${url.pathname} -> VPC backend`);
	console.log(`[MCP Proxy] MCP_SECRET set:`, !!env.MCP_SECRET, `length:`, env.MCP_SECRET?.length || 0);

	// Clone headers and add our shared secret
	const headers = new Headers();

	// Copy relevant headers from the original request
	for (const [key, value] of request.headers.entries()) {
		// Skip hop-by-hop headers and authorization (we use X-MCP-Secret instead)
		if (!['host', 'authorization', 'connection', 'keep-alive', 'transfer-encoding'].includes(key.toLowerCase())) {
			headers.set(key, value);
		}
	}

	headers.set("X-MCP-Secret", env.MCP_SECRET);

	try {
		// Use VPC binding instead of public fetch
		const response = await env.MCP_BACKEND.fetch(backendUrl, {
			method: request.method,
			headers,
			body: request.body,
			// @ts-ignore - duplex needed for streaming
			duplex: "half",
		});

		const body = await response.text();
		console.log(`[MCP Proxy] Backend response: ${response.status} - ${body.substring(0, 200)}`);
		return new Response(body, {
			status: response.status,
			headers: response.headers
		});
	} catch (error) {
		console.error(`[MCP Proxy] Backend error:`, error);
		return new Response(JSON.stringify({
			jsonrpc: "2.0",
			id: "proxy-error",
			error: { code: -32000, message: `Backend error: ${error}` }
		}), {
			status: 502,
			headers: { "Content-Type": "application/json" }
		});
	}
}

// Create a route handler for /mcp that proxies to the backend
function createMCPHandler(path: string) {
	return {
		fetch: async (request: Request, env: Env, _ctx: ExecutionContext, authProps: AuthProps<Props>) => {
			const url = new URL(request.url);
			if (url.pathname === path || url.pathname.startsWith(path + "/")) {
				return handleMCPProxy(request, env, authProps);
			}
			return new Response("Not Found", { status: 404 });
		}
	};
}

// Health check that tests backend connectivity via VPC (no auth required)
async function handleHealthCheck(request: Request, env: Env): Promise<Response | null> {
	const url = new URL(request.url);
	if (url.pathname !== "/health") return null;

	try {
		// Use VPC binding for health check
		const response = await env.MCP_BACKEND.fetch("http://mcp-router.internal/mcp", {
			method: "POST",
			headers: {
				"Content-Type": "application/json",
				"Accept": "application/json, text/event-stream",
				"X-MCP-Secret": env.MCP_SECRET,
			},
			body: JSON.stringify({
				jsonrpc: "2.0",
				id: "health",
				method: "initialize",
				params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "health-check", version: "1.0" } }
			}),
		});
		const body = await response.text();
		return new Response(JSON.stringify({
			status: "ok",
			backend_status: response.status,
			backend_response: body.substring(0, 200),
		}), {
			headers: { "Content-Type": "application/json" }
		});
	} catch (error) {
		return new Response(JSON.stringify({
			status: "error",
			error: String(error),
		}), {
			status: 502,
			headers: { "Content-Type": "application/json" }
		});
	}
}

const oauthProvider = new OAuthProvider({
	apiHandler: createMCPHandler("/mcp"),
	apiRoute: "/mcp",
	authorizeEndpoint: "/authorize",
	clientRegistrationEndpoint: "/register",
	defaultHandler: GitHubHandler as any,
	tokenEndpoint: "/token",
	accessTokenTTL: 86400,
	refreshTokenTTL: 2592000,
});

export default {
	async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
		// Check health endpoint first (no auth)
		const healthResponse = await handleHealthCheck(request, env);
		if (healthResponse) return healthResponse;

		// Otherwise delegate to OAuth provider
		return oauthProvider.fetch(request, env, ctx);
	}
};
