import { visit } from 'unist-util-visit';
import type { Root, Element, Text } from 'hast';

/**
 * rehype plugin to apply roleplay specific formatting.
 * 
 * 1. Hides 「 and 」 brackets
 * 2. Greys out text inside （ ） or ( )
 */
export function rehypeRoleplayFormat() {
	return (tree: Root) => {
		// 1. Convert em tags (which are parsed *text*) to grey span
		visit(tree, 'element', (node) => {
			if (node.tagName === 'em') {
				node.tagName = 'span';
				node.properties = { className: ['italic', 'text-muted-foreground', 'opacity-70'] };
			}
		});

		// 2. Parse plain text *text* that wasn't converted to em
		visit(tree, 'text', (node, index, parent) => {
			if (!parent || typeof node.value !== 'string') return;

			if (parent.tagName === 'code' || parent.tagName === 'pre') {
				return;
			}

			let value = node.value;
			let changed = false;

			// Remove 「 and 」
			if (value.includes('「') || value.includes('」')) {
				value = value.replace(/[「」]/g, '');
				changed = true;
			}

			// Grey out text inside *...* and remove the asterisks themselves
			const regex = /(\*.*?\*)/g;
			if (regex.test(value)) {
				const parts = value.split(regex);
				const newNodes: (Text | Element)[] = parts
					.map((part) => {
						if (part.startsWith('*') && part.endsWith('*')) {
							const innerText = part.slice(1, -1);
							return {
								type: 'element',
								tagName: 'span',
								properties: { className: ['italic', 'text-muted-foreground', 'opacity-70'] },
								children: [{ type: 'text', value: innerText }]
							} as Element;
						} else {
							return { type: 'text', value: part } as Text;
						}
					})
					.filter((n) => n.type !== 'text' || n.value !== '');

				if (index !== undefined) {
					parent.children.splice(index, 1, ...newNodes);
					return index + newNodes.length; // skip newly added nodes
				}
			} else if (changed) {
				node.value = value;
			}
		});
	};
}
