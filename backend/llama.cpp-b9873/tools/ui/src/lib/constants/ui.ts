import { Search, Settings, SquarePen, Brain, Terminal, Volume2, Sparkles } from '@lucide/svelte';
import McpLogo from '$lib/components/app/mcp/McpLogo.svelte';
import type { Component } from 'svelte';
import { ROUTES } from './routes';

export const FORK_TREE_DEPTH_PADDING = 8;
export const SYSTEM_MESSAGE_PLACEHOLDER = 'System message';

export const ICON_STRIP_TRANSITION_DURATION = 150;
export const ICON_STRIP_TRANSITION_DELAY_MULTIPLIER = 50;

export interface DesktopIconStripItem {
	icon: Component;
	tooltip: string;
	tooltipKey?: string;
	route?: string;
	activeRouteId?: string;
	activeRoutePrefix?: string;
	activeUrlIncludes?: string;
	keys?: string[];
	action?: string;
}

export const SIDEBAR_ACTIONS_ITEMS: DesktopIconStripItem[] = [
	{ icon: SquarePen, tooltip: 'New chat', tooltipKey: 'sidebar.newChat', route: ROUTES.NEW_CHAT, keys: ['shift', 'cmd', 'o'] },
	{ icon: Search, tooltip: 'Search', tooltipKey: 'sidebar.search', keys: ['cmd', 'k'] },
	{
		icon: McpLogo,
		tooltip: 'MCP Servers',
		tooltipKey: 'sidebar.mcpServers',
		route: ROUTES.MCP_SERVERS,
		activeRouteId: '/mcp-servers'
	},
	{
		icon: Settings,
		tooltip: 'Settings',
		tooltipKey: 'sidebar.settings',
		route: `${ROUTES.SETTINGS}/general`,
		activeUrlIncludes: '#/settings'
	},
	{
		icon: Brain,
		tooltip: 'RAG Memory & Summary',
		tooltipKey: 'sidebar.ragMemory',
		action: 'open_rag_panel'
	},
	{
		icon: Volume2,
		tooltip: 'TTS & Voice Settings',
		tooltipKey: 'sidebar.ttsSettings',
		action: 'open_tts_panel'
	},
	{
		icon: Terminal,
		tooltip: 'Debug',
		tooltipKey: 'sidebar.debug',
		action: 'open_debug_panel'
	},
	{
		icon: Sparkles,
		tooltip: 'Initial Setup Wizard',
		tooltipKey: 'sidebar.wizard',
		action: 'open_initial_setup'
	}
];
