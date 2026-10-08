import assert from 'node:assert/strict';
import { test } from 'node:test';
import { renderMarkdown } from '../src/lib/server/markdown';

test('agent output remains readable without executing HTML or loading remote images', () => {
    const html = renderMarkdown('# Findings\n\n**Read this** and [source](https://example.com).\n\n<script>alert(1)</script><iframe src="https://example.com"></iframe><img src="https://example.com/tracker" onerror="alert(1)">\n\n[attack](javascript:alert(1))\n\n| A | B |\n|---|---|\n| 1 | 2 |');
    assert.match(html, /<h1>Findings<\/h1>/);
    assert.match(html, /<strong>Read this<\/strong>/);
    assert.match(html, /<table>/);
    assert.match(html, /href="https:\/\/example.com"/);
    assert(!/<script|<iframe|<img|onerror|javascript:/i.test(html));
});
