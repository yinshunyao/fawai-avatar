// Markdown rendering support for chat messages
// Auto-initializes when DOM is ready

(function() {
    'use strict';

    // Load marked.js from CDN if not already loaded
    function loadMarkedLibrary() {
        return new Promise((resolve, reject) => {
            if (typeof marked !== 'undefined') {
                resolve();
                return;
            }
            const script = document.createElement('script');
            script.src = 'https://cdn.jsdelivr.net/npm/marked/marked.min.js';
            script.onload = resolve;
            script.onerror = reject;
            document.head.appendChild(script);
        });
    }

    // Load highlight.js for code syntax highlighting
    function loadHighlightLibrary() {
        return new Promise((resolve, reject) => {
            if (typeof hljs !== 'undefined') {
                resolve();
                return;
            }
            // Load CSS
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = 'https://cdn.jsdelivr.net/gh/highlightjs/cdn-release/build/styles/github.min.css';
            document.head.appendChild(link);
            
            // Load JS
            const script = document.createElement('script');
            script.src = 'https://cdn.jsdelivr.net/gh/highlightjs/cdn-release/build/highlight.min.js';
            script.onload = resolve;
            script.onerror = reject;
            document.head.appendChild(script);
        });
    }

    // Configure marked options
    function configureMarked() {
        if (typeof marked === 'undefined') return;
        
        marked.setOptions({
            breaks: true,        // Convert \n to <br>
            gfm: true,          // GitHub Flavored Markdown
            headerIds: false,   // Don't add IDs to headers
            mangle: false,      // Don't escape email addresses
            sanitize: false,    // We'll handle sanitization
            smartLists: true,
            smartypants: false,
            highlight: function(code, lang) {
                if (typeof hljs !== 'undefined' && lang && hljs.getLanguage(lang)) {
                    try {
                        return hljs.highlight(code, { language: lang }).value;
                    } catch (e) {
                        console.warn('Highlight error:', e);
                    }
                }
                return code;
            }
        });
    }

    // Render markdown text to HTML
    function renderMarkdown(text) {
        if (typeof marked === 'undefined') return text;
        
        try {
            // Trim so trailing newlines from marked don't become extra blank lines in the bubble
            const html = marked.parse(text);
            return String(html || "").trim();
        } catch (e) {
            console.error('Markdown render error:', e);
            return text;
        }
    }

    // Process a single message element
    function processMessageElement(element) {
        if (!element || element.dataset.markdownProcessed) return;
        
        const text = element.textContent || element.innerText;
        if (!text || text.trim() === '') return;
        
        // Check if it looks like it might contain markdown
        const hasMarkdown = /[#*`_\[\]{}()~>|\\-]/.test(text);
        if (!hasMarkdown) return;
        
        const html = renderMarkdown(text);
        element.innerHTML = html;
        element.dataset.markdownProcessed = 'true';
        
        // Apply syntax highlighting to code blocks
        if (typeof hljs !== 'undefined') {
            element.querySelectorAll('pre code').forEach((block) => {
                hljs.highlightElement(block);
            });
        }
    }

    // Find and process all message elements
    function processAllMessages() {
        // Common selectors for chat message containers
        const selectors = [
            '.message-content',
            '.message-text',
            '.chat-message',
            '.message-body',
            '[data-message]',
            '.msg-content',
            '.message p',
            '.assistant-message',
            '.user-message'
        ];
        
        selectors.forEach(selector => {
            document.querySelectorAll(selector).forEach(processMessageElement);
        });
    }

    // Create a MutationObserver to watch for new messages
    function setupObserver() {
        const observer = new MutationObserver((mutations) => {
            let shouldProcess = false;
            
            mutations.forEach((mutation) => {
                if (mutation.addedNodes.length) {
                    mutation.addedNodes.forEach((node) => {
                        if (node.nodeType === Node.ELEMENT_NODE) {
                            // Check if this is a message element or contains one
                            if (node.matches && (
                                node.matches('.message-content, .message-text, .chat-message, .message-body, [data-message], .msg-content') ||
                                node.querySelector('.message-content, .message-text, .chat-message, .message-body, [data-message], .msg-content')
                            )) {
                                shouldProcess = true;
                            }
                        }
                    });
                }
            });
            
            if (shouldProcess) {
                // Debounce processing
                clearTimeout(window.markdownProcessTimeout);
                window.markdownProcessTimeout = setTimeout(processAllMessages, 100);
            }
        });
        
        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
    }

    // Initialize markdown support
    async function initialize() {
        try {
            // Load required libraries
            await Promise.all([
                loadMarkedLibrary(),
                loadHighlightLibrary()
            ]);
            
            // Configure marked
            configureMarked();
            
            // Process existing messages
            processAllMessages();
            
            // Setup observer for new messages
            setupObserver();
            
            console.log('✓ Markdown rendering initialized');
        } catch (e) {
            console.error('Failed to initialize markdown rendering:', e);
        }
    }

    // Start when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialize);
    } else {
        initialize();
    }

    // Expose API for manual use
    window.MarkdownRenderer = {
        render: renderMarkdown,
        processElement: processMessageElement,
        processAll: processAllMessages,
        reinitialize: initialize
    };

    // Expose as global function for direct calls from index.html
    window.renderMarkdown = renderMarkdown;
})();
