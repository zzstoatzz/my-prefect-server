import { marked } from 'marked';
import sanitizeHtml from 'sanitize-html';

export function renderMarkdown(source: string): string {
    return sanitizeHtml(marked.parse(source, { async: false }), {
        allowedTags: ['p', 'br', 'h1', 'h2', 'h3', 'h4', 'strong', 'em', 'del', 'ul', 'ol', 'li', 'blockquote', 'pre', 'code', 'a', 'hr', 'table', 'thead', 'tbody', 'tr', 'th', 'td'],
        allowedAttributes: { a: ['href', 'rel', 'target'], ol: ['start'] },
        allowedSchemes: ['https', 'http'],
        allowProtocolRelative: false,
        transformTags: { a: sanitizeHtml.simpleTransform('a', { rel: 'noopener noreferrer', target: '_blank' }) }
    });
}
