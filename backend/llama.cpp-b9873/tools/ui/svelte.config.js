import { mdsvex } from 'mdsvex';
import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const defaultOutDir = path.resolve(__dirname, '../../../../llama-b9873-bin-win-cuda-13.3-x64sandbox/custom_public');
const outDir = process.env.LLAMA_UI_OUT_DIR || defaultOutDir;

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: [vitePreprocess(), mdsvex()],

	kit: {
		paths: {
			relative: true
		},
		router: { type: 'hash' },
		adapter: adapter({
			pages: outDir,
			assets: outDir,
			fallback: 'index.html',
			precompress: false,
			strict: true
		}),
		output: {
			bundleStrategy: 'single'
		},
		alias: {
			$styles: 'src/styles'
		}
	},

	extensions: ['.svelte', '.svx']
};

export default config;