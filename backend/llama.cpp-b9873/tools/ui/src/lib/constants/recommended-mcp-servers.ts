import { DEFAULT_MCP_CONFIG } from './mcp';
import type { RecommendedMCPServer } from '$lib/types';

/**
 * Pre-defined recommended MCP servers.
 *
 * Servers are enabled by default, but they are not turned on for individual
 * conversations until the user explicitly enables them (so their tools are
 * disabled by default).
 */
export const RECOMMENDED_MCP_SERVERS: RecommendedMCPServer[] = [];

export const RECOMMENDED_MCP_SERVER_IDS = new Set(
	RECOMMENDED_MCP_SERVERS.map((server) => server.id)
);

export const RECOMMENDED_MCP_SERVERS_OPTIN_DIALOG_DELAY = 1000;
