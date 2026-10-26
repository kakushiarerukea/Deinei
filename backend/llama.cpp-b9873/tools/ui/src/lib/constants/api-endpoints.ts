export const API_MODELS = {
	LIST: '/v1/models',
	LOAD: '/models/load',
	UNLOAD: '/models/unload',
	SSE: '/models/sse'
};

export const isGatewayMode = (): boolean => {
	if (typeof window !== 'undefined' && window.location) {
		return window.location.port !== '8080';
	}
	return false;
};

export const getBackendHost = (): string => {
	if (typeof window !== 'undefined' && window.location.hostname) {
		return window.location.hostname;
	}
	return '127.0.0.1';
};

export const getGatewayBaseUrl = (): string => {
	if (isGatewayMode()) {
		return '/__deinei/api';
	}
	return `http://${getBackendHost()}:5000`;
};

export const getTtsBaseUrl = (): string => {
	if (isGatewayMode()) {
		return '/__deinei/tts';
	}
	return `http://${getBackendHost()}:5001`;
};

// chat completion routes, the control route drives realtime inference (e.g. end reasoning)
// 泥濘統合: チャット完了リクエストをFlaskプロキシ経由に変更（RAG/Web検索注入のため）
export const API_CHAT = {
	get COMPLETIONS(): string {
		return `${getGatewayBaseUrl()}/v1/chat/completions`;
	},
	get CONTROL(): string {
		return `${getGatewayBaseUrl()}/v1/chat/completions/control`;
	}
};

// slot introspection, requires the --slots flag on the server
export const API_SLOTS = {
	LIST: './slots'
};

export const API_TOOLS = {
	LIST: '/tools',
	EXECUTE: '/tools'
};

// resumable stream routes, the conv::model identity is appended as a path segment
export const API_STREAM = {
	BASE: './v1/stream',
	LOOKUP: './v1/streams/lookup'
};

/** CORS proxy endpoint path */
export const CORS_PROXY_ENDPOINT = '/cors-proxy';
