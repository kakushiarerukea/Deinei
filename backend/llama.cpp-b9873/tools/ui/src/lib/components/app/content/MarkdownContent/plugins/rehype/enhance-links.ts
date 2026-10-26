/**
 * Rehype plugin to enhance links with security attributes.
 *
 * Adds target="_blank" and rel="noopener noreferrer" to all anchor elements,
 * ensuring external links open in new tabs safely.
 * Also sanitizes unsafe protocols like javascript: or data: to prevent XSS.
 */

import type { Plugin } from 'unified';
import type { Root, Element } from 'hast';
import { visit } from 'unist-util-visit';

/**
 * Rehype plugin that adds security attributes to all links.
 * This plugin ensures external links open in new tabs safely by adding:
 * - target="_blank"
 * - rel="noopener noreferrer"
 * - sanitizing dangerous URI schemes (javascript:, data:, vbscript:)
 */
export const rehypeEnhanceLinks: Plugin<[], Root> = () => {
	return (tree: Root) => {
		visit(tree, 'element', (node: Element) => {
			if (node.tagName !== 'a') return;

			const props = node.properties ?? {};

			// Only modify if href exists
			if (!props.href) return;

			const href = String(props.href).trim();
			// Sanitize dangerous schemes (javascript:, data:, vbscript:, etc.)
			const isAllowedScheme =
				href.startsWith('#') ||
				href.startsWith('/') ||
				/^https?:\/\//i.test(href) ||
				/^mailto:/i.test(href);

			if (!isAllowedScheme) {
				props.href = '#';
				node.properties = props;
				return;
			}

			props.target = '_blank';
			props.rel = 'noopener noreferrer';
			node.properties = props;
		});
	};
};
