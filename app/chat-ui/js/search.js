/**
 * Progenitor Chat UI - Search Module
 * 
 * Handles searching through chat messages.
 */

export class SearchManager {
    constructor(chatManager) {
        this.chatManager = chatManager;
        this.isOpen = false;
        this.currentQuery = '';
        this.matches = [];
        this.currentMatchIndex = -1;
        
        this.init();
    }

    init() {
        this.createSearchBar();
        this.bindKeyboardShortcuts();
    }

    createSearchBar() {
        const searchBar = document.createElement('div');
        searchBar.id = 'search-bar';
        searchBar.className = 'search-bar hidden';
        searchBar.innerHTML = `
            <div class="search-container">
                <input type="text" 
                       class="search-input" 
                       placeholder="Search messages..." 
                       aria-label="Search messages">
                <span class="search-count"></span>
                <div class="search-nav">
                    <button class="search-nav-btn" id="search-prev" title="Previous match (Shift+Enter)">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="18 15 12 9 6 15"></polyline>
                        </svg>
                    </button>
                    <button class="search-nav-btn" id="search-next" title="Next match (Enter)">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="6 9 12 15 18 9"></polyline>
                        </svg>
                    </button>
                </div>
                <button class="search-close-btn" id="search-close" title="Close (Escape)">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <line x1="18" y1="6" x2="6" y2="18"></line>
                        <line x1="6" y1="6" x2="18" y2="18"></line>
                    </svg>
                </button>
            </div>
        `;

        // Insert before chat container
        const header = document.querySelector('.header');
        if (header) {
            header.parentNode.insertBefore(searchBar, header.nextSibling);
        }

        this.searchBar = searchBar;
        this.searchInput = searchBar.querySelector('.search-input');
        this.searchCount = searchBar.querySelector('.search-count');

        // Bind events
        this.searchInput.addEventListener('input', (e) => this.handleSearchInput(e));
        this.searchInput.addEventListener('keydown', (e) => this.handleSearchKeydown(e));
        
        searchBar.querySelector('#search-prev').addEventListener('click', () => this.navigateMatch(-1));
        searchBar.querySelector('#search-next').addEventListener('click', () => this.navigateMatch(1));
        searchBar.querySelector('#search-close').addEventListener('click', () => this.close());
    }

    bindKeyboardShortcuts() {
        document.addEventListener('keydown', (e) => {
            // Ctrl+F to open search
            if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
                // Don't prevent default if input is focused
                if (!['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) {
                    e.preventDefault();
                    this.open();
                }
            }
        });
    }

    open() {
        this.isOpen = true;
        this.searchBar.classList.remove('hidden');
        this.searchInput.focus();
        this.searchInput.select();
    }

    close() {
        this.isOpen = false;
        this.searchBar.classList.add('hidden');
        this.clearHighlights();
        this.currentQuery = '';
        this.matches = [];
        this.currentMatchIndex = -1;
        this.updateCount();
        
        // Return focus to chat input
        document.getElementById('chat-input')?.focus();
    }

    handleSearchInput(e) {
        const query = e.target.value.trim().toLowerCase();
        
        if (query === this.currentQuery) return;
        
        this.currentQuery = query;
        this.clearHighlights();
        
        if (!query) {
            this.matches = [];
            this.currentMatchIndex = -1;
            this.updateCount();
            return;
        }

        this.search(query);
    }

    handleSearchKeydown(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            if (e.shiftKey) {
                this.navigateMatch(-1);
            } else {
                this.navigateMatch(1);
            }
        } else if (e.key === 'Escape') {
            this.close();
        }
    }

    search(query) {
        this.matches = [];
        const messages = document.querySelectorAll('.message');

        messages.forEach((msg, index) => {
            const content = msg.querySelector('.message-content');
            if (!content) return;

            const text = content.textContent.toLowerCase();
            const positions = [];
            let pos = text.indexOf(query);

            while (pos !== -1) {
                positions.push(pos);
                pos = text.indexOf(query, pos + 1);
            }

            if (positions.length > 0) {
                this.matches.push({
                    element: msg,
                    contentElement: content,
                    messageIndex: index,
                    positions
                });
            }
        });

        if (this.matches.length > 0) {
            this.currentMatchIndex = 0;
            this.highlightMatches();
            this.scrollToMatch(0);
        }

        this.updateCount();
    }

    highlightMatches() {
        this.matches.forEach((match, matchIndex) => {
            const content = match.contentElement;
            const originalHTML = content.innerHTML;
            const text = content.textContent;
            const query = this.currentQuery;

            // Create highlighted version
            let highlightedHTML = '';
            let lastIndex = 0;
            let textLower = text.toLowerCase();
            let pos = textLower.indexOf(query);
            let instanceCount = 0;

            while (pos !== -1) {
                highlightedHTML += this.escapeHTML(text.substring(lastIndex, pos));
                const matchedText = text.substring(pos, pos + query.length);
                const isCurrentMatch = matchIndex === this.currentMatchIndex && 
                                       instanceCount === 0; // First instance
                
                highlightedHTML += `<mark class="search-highlight${isCurrentMatch ? ' current' : ''}">${this.escapeHTML(matchedText)}</mark>`;
                
                lastIndex = pos + query.length;
                pos = textLower.indexOf(query, lastIndex);
                instanceCount++;
            }

            highlightedHTML += this.escapeHTML(text.substring(lastIndex));
            
            // Store original and apply highlight
            content.dataset.originalHTML = originalHTML;
            content.innerHTML = highlightedHTML;
        });
    }

    clearHighlights() {
        document.querySelectorAll('.message-content[data-original-html]').forEach(el => {
            el.innerHTML = el.dataset.originalHTML;
            delete el.dataset.originalHTML;
        });
    }

    escapeHTML(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    navigateMatch(direction) {
        if (this.matches.length === 0) return;

        // Remove current highlight
        document.querySelectorAll('.search-highlight.current').forEach(el => {
            el.classList.remove('current');
        });

        // Calculate new index
        this.currentMatchIndex += direction;
        if (this.currentMatchIndex < 0) {
            this.currentMatchIndex = this.matches.length - 1;
        } else if (this.currentMatchIndex >= this.matches.length) {
            this.currentMatchIndex = 0;
        }

        // Re-highlight all and mark current
        this.clearHighlights();
        this.highlightMatches();
        this.scrollToMatch(this.currentMatchIndex);
        this.updateCount();
    }

    scrollToMatch(index) {
        if (index < 0 || index >= this.matches.length) return;

        const match = this.matches[index];
        match.element.scrollIntoView({
            behavior: 'smooth',
            block: 'center'
        });

        // Flash the message
        match.element.classList.add('search-flash');
        setTimeout(() => {
            match.element.classList.remove('search-flash');
        }, 500);
    }

    updateCount() {
        if (this.matches.length === 0) {
            this.searchCount.textContent = this.currentQuery ? 'No results' : '';
        } else {
            this.searchCount.textContent = `${this.currentMatchIndex + 1} of ${this.matches.length}`;
        }
    }
}

export default SearchManager;
