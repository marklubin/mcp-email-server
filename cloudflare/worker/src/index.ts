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
 * server running on oxnard via Cloudflare Tunnel.
 */
async function handleMCPProxy(request: Request, env: Env, _authProps: AuthProps<Props>): Promise<Response> {
	const url = new URL(request.url);
	const backendUrl = new URL(url.pathname + url.search, env.BACKEND_URL);

	// Clone headers and add our shared secret
	const headers = new Headers(request.headers);
	headers.set("X-MCP-Secret", env.MCP_SECRET);

	const backendRequest = new Request(backendUrl.toString(), {
		method: request.method,
		headers,
		body: request.body,
		// @ts-ignore - duplex needed for streaming
		duplex: "half",
	});

	return fetch(backendRequest);
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

export default new OAuthProvider({
	apiHandler: createMCPHandler("/mcp"),
	apiRoute: "/mcp",
	authorizeEndpoint: "/authorize",
	clientRegistrationEndpoint: "/register",
	defaultHandler: GitHubHandler as any,
	tokenEndpoint: "/token",
});
