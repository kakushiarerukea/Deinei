import { visit } from 'unist-util-visit';
import type { Root, Element, Text } from 'hast';

/**
 * rehype plugin to apply roleplay specific formatting.
 * 
 * 1. Hides 「 and 」 brackets
 * 2. Greys out text inside （ ） or ( ) / *...*
 * 3. Hides emojis used as avatar expression triggers from chat display
 */
export function rehypeRoleplayFormat() {
	return (tree: Root) => {
		if (typeof localStorage !== 'undefined' && localStorage.getItem('deining_show_raw_text') === 'true') {
			return;
		}
		// 1. Convert em tags (which are parsed *text*) to grey span
		visit(tree, 'element', (node) => {
			if (node.tagName === 'em') {
				node.tagName = 'span';
				node.properties = { className: ['italic', 'text-muted-foreground', 'opacity-70'] };
			}
		});

		// 絵文字の正規表現（一般的な記号 ♪、★、☆、♡ などは残しつつ、表情・絵文字類を画面表示から除去）
		const emojiRegex = /(?:\p{Emoji_Presentation}|\p{Emoji}\uFE0F|\p{Extended_Pictographic}(?<![♪★☆♡]))(?:\uFE0F|\p{Emoji_Modifier}|\u200D(?:\p{Emoji_Presentation}|\p{Emoji}\uFE0F|\p{Extended_Pictographic}(?<![♪★☆♡])))*/gu;

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

			// 誤出力された [emotion: ...] 形式のタグをフロント表示から除去
			const tagRegex = /\[[a-zA-Z_]+:[^\]]*\]/g;
			if (tagRegex.test(value)) {
				value = value.replace(tagRegex, '');
				changed = true;
			}

			// 表情トリガー用の絵文字をフロント表示から除去
			if (emojiRegex.test(value)) {
				value = value.replace(emojiRegex, '');
				// 絵文字除去後の余分な半角空白（文末や句読点の手前）を整形
				value = value.replace(/[ \t]+([。、！？!?\n])/g, '$1').replace(/[ \t]+$/gm, '');
				changed = true;
				emojiRegex.lastIndex = 0;
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

