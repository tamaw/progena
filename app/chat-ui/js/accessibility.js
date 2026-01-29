/**
 * Progenitor Chat UI - Accessibility Module
 * 
 * Provides ARIA labels, screen reader support, and accessibility features.
 */

export class AccessibilityManager {
    constructor() {
        this.announcer = null;
        this.focusTrap = null;
        this.skipLinks = null;
        this.reducedMotion = false;
        
        this.init();
    }

    init() {
        this.detectPreferences();
        this.createAnnouncer();
        this.createSkipLinks();
        this.enhanceExistingElements();
        this.setupFocusManagement();
        this.bindKeyboardNavigation();
    }

    /**
     * Detect user preferences.
     */
    detectPreferences() {
        // Reduced motion preference
        this.reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        
        if (this.reducedMotion) {
            document.documentElement.classList.add('reduce-motion');
        }

        // Listen for changes
        window.matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', (e) => {
            this.reducedMotion = e.matches;
            document.documentElement.classList.toggle('reduce-motion', e.matches);
        });

        // High contrast preference
        const highContrast = window.matchMedia('(prefers-contrast: more)').matches;
        if (highContrast) {
            document.documentElement.classList.add('high-contrast');
        }
    }

    /**
     * Create live region for screen reader announcements.
     */
    createAnnouncer() {
        const announcer = document.createElement('div');
        announcer.id = 'sr-announcer';
        announcer.setAttribute('role', 'status');
        announcer.setAttribute('aria-live', 'polite');
        announcer.setAttribute('aria-atomic', 'true');
        announcer.className = 'sr-only';
        document.body.appendChild(announcer);
        
        this.announcer = announcer;

        // Urgent announcer
        const urgentAnnouncer = document.createElement('div');
        urgentAnnouncer.id = 'sr-announcer-urgent';
        urgentAnnouncer.setAttribute('role', 'alert');
        urgentAnnouncer.setAttribute('aria-live', 'assertive');
        urgentAnnouncer.setAttribute('aria-atomic', 'true');
        urgentAnnouncer.className = 'sr-only';
        document.body.appendChild(urgentAnnouncer);
        
        this.urgentAnnouncer = urgentAnnouncer;
    }

    /**
     * Announce message to screen readers.
     * @param {string} message - Message to announce
     * @param {boolean} urgent - Use assertive live region
     */
    announce(message, urgent = false) {
        const announcer = urgent ? this.urgentAnnouncer : this.announcer;
        if (!announcer) return;

        // Clear and set message (ensures announcement even for same text)
        announcer.textContent = '';
        setTimeout(() => {
            announcer.textContent = message;
        }, 50);
    }

    /**
     * Create skip links for keyboard users.
     */
    createSkipLinks() {
        const skipNav = document.createElement('nav');
        skipNav.className = 'skip-links';
        skipNav.setAttribute('aria-label', 'Skip navigation');
        skipNav.innerHTML = `
            <a href="#chat-messages" class="skip-link">Skip to messages</a>
            <a href="#chat-input" class="skip-link">Skip to input</a>
            <a href="#sidebar" class="skip-link">Skip to sidebar</a>
        `;
        
        document.body.insertBefore(skipNav, document.body.firstChild);
        this.skipLinks = skipNav;
    }

    /**
     * Enhance existing elements with ARIA attributes.
     */
    enhanceExistingElements() {
        // Main regions
        this.setLandmark('.chat-container', 'main', 'Chat conversation');
        this.setLandmark('.sidebar', 'complementary', 'Chat sidebar');
        this.setLandmark('.status-bar', 'contentinfo', 'Status information');
        
        // Chat messages container
        const messagesContainer = document.querySelector('.chat-messages');
        if (messagesContainer) {
            messagesContainer.setAttribute('role', 'log');
            messagesContainer.setAttribute('aria-label', 'Chat messages');
            messagesContainer.setAttribute('aria-live', 'polite');
            messagesContainer.id = 'chat-messages';
        }

        // Input area
        const inputArea = document.querySelector('.chat-input textarea');
        if (inputArea) {
            inputArea.setAttribute('aria-label', 'Message input');
            inputArea.setAttribute('aria-describedby', 'input-hint');
            inputArea.id = 'chat-input';

            // Add input hint
            const hint = document.createElement('span');
            hint.id = 'input-hint';
            hint.className = 'sr-only';
            hint.textContent = 'Press Enter to send, Shift+Enter for new line';
            inputArea.parentElement?.appendChild(hint);
        }

        // Send button
        const sendBtn = document.querySelector('#send-btn');
        if (sendBtn) {
            sendBtn.setAttribute('aria-label', 'Send message');
        }

        // Settings button
        const settingsBtn = document.querySelector('#settings-btn');
        if (settingsBtn) {
            settingsBtn.setAttribute('aria-label', 'Open settings');
            settingsBtn.setAttribute('aria-haspopup', 'dialog');
        }

        // Sidebar toggle
        const sidebarToggle = document.querySelector('.sidebar-toggle');
        if (sidebarToggle) {
            sidebarToggle.setAttribute('aria-label', 'Toggle sidebar');
            sidebarToggle.setAttribute('aria-expanded', 'true');
        }

        // Sidebar
        const sidebar = document.querySelector('.sidebar');
        if (sidebar) {
            sidebar.id = 'sidebar';
        }

        // Enhance all buttons without labels
        document.querySelectorAll('button:not([aria-label])').forEach(btn => {
            const text = btn.textContent.trim();
            if (!text) {
                const svg = btn.querySelector('svg');
                const title = btn.title || svg?.querySelector('title')?.textContent;
                if (title) {
                    btn.setAttribute('aria-label', title);
                }
            }
        });
    }

    /**
     * Set ARIA landmark role.
     */
    setLandmark(selector, role, label) {
        const el = document.querySelector(selector);
        if (el) {
            el.setAttribute('role', role);
            if (label) {
                el.setAttribute('aria-label', label);
            }
        }
    }

    /**
     * Setup focus management.
     */
    setupFocusManagement() {
        // Focus visible outline
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Tab') {
                document.body.classList.add('keyboard-nav');
            }
        });

        document.addEventListener('mousedown', () => {
            document.body.classList.remove('keyboard-nav');
        });

        // Focus trap for modals
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Tab' && this.focusTrap) {
                this.handleFocusTrap(e);
            }
        });
    }

    /**
     * Trap focus within an element.
     * @param {HTMLElement} element - Element to trap focus in
     */
    trapFocus(element) {
        this.focusTrap = element;
        
        // Find all focusable elements
        const focusable = element.querySelectorAll(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        
        if (focusable.length > 0) {
            focusable[0].focus();
        }
    }

    /**
     * Release focus trap.
     */
    releaseFocusTrap() {
        this.focusTrap = null;
    }

    /**
     * Handle focus trap tab key.
     */
    handleFocusTrap(e) {
        if (!this.focusTrap) return;

        const focusable = this.focusTrap.querySelectorAll(
            'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        );
        
        const first = focusable[0];
        const last = focusable[focusable.length - 1];

        if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
        }
    }

    /**
     * Bind keyboard navigation.
     */
    bindKeyboardNavigation() {
        // Arrow key navigation in message list
        document.addEventListener('keydown', (e) => {
            const messages = document.querySelector('.chat-messages');
            if (!messages?.contains(document.activeElement)) return;

            const messageEls = Array.from(messages.querySelectorAll('.message'));
            const currentIndex = messageEls.indexOf(document.activeElement.closest('.message'));

            if (e.key === 'ArrowUp' && currentIndex > 0) {
                e.preventDefault();
                this.focusMessage(messageEls[currentIndex - 1]);
            } else if (e.key === 'ArrowDown' && currentIndex < messageEls.length - 1) {
                e.preventDefault();
                this.focusMessage(messageEls[currentIndex + 1]);
            } else if (e.key === 'Home') {
                e.preventDefault();
                this.focusMessage(messageEls[0]);
            } else if (e.key === 'End') {
                e.preventDefault();
                this.focusMessage(messageEls[messageEls.length - 1]);
            }
        });
    }

    /**
     * Focus a message and make it tabbable.
     */
    focusMessage(messageEl) {
        if (!messageEl) return;

        // Remove tabindex from other messages
        document.querySelectorAll('.message[tabindex="0"]').forEach(el => {
            el.setAttribute('tabindex', '-1');
        });

        // Make this message tabbable and focus it
        messageEl.setAttribute('tabindex', '0');
        messageEl.focus();

        // Announce message content
        const role = messageEl.classList.contains('user') ? 'You' : 'Assistant';
        const content = messageEl.querySelector('.message-content')?.textContent || '';
        this.announce(`${role}: ${content.substring(0, 100)}${content.length > 100 ? '...' : ''}`);
    }

    /**
     * Announce new message.
     * @param {string} role - Message role (user/assistant)
     * @param {string} content - Message content
     */
    announceMessage(role, content) {
        const roleLabel = role === 'user' ? 'You said' : 'Assistant replied';
        const preview = content.substring(0, 150);
        this.announce(`${roleLabel}: ${preview}${content.length > 150 ? '...' : ''}`);
    }

    /**
     * Announce error.
     * @param {string} message - Error message
     */
    announceError(message) {
        this.announce(`Error: ${message}`, true);
    }

    /**
     * Announce status change.
     * @param {string} status - Status message
     */
    announceStatus(status) {
        this.announce(status);
    }
}

// Add reduced motion CSS
const style = document.createElement('style');
style.textContent = `
    /* Screen reader only class */
    .sr-only {
        position: absolute !important;
        width: 1px !important;
        height: 1px !important;
        padding: 0 !important;
        margin: -1px !important;
        overflow: hidden !important;
        clip: rect(0, 0, 0, 0) !important;
        white-space: nowrap !important;
        border: 0 !important;
    }

    /* Skip links */
    .skip-links {
        position: absolute;
        top: 0;
        left: 0;
        z-index: 9999;
    }

    .skip-link {
        position: absolute;
        left: -9999px;
        top: 0;
        padding: 8px 16px;
        background-color: var(--color-accent);
        color: white;
        text-decoration: none;
        font-weight: 600;
        border-radius: 0 0 4px 0;
    }

    .skip-link:focus {
        left: 0;
        outline: 2px solid var(--color-accent);
        outline-offset: 2px;
    }

    /* Focus visible for keyboard navigation */
    .keyboard-nav *:focus {
        outline: 2px solid var(--color-accent) !important;
        outline-offset: 2px !important;
    }

    /* Reduced motion */
    .reduce-motion * {
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
    }

    /* High contrast mode enhancements */
    .high-contrast {
        --color-border: #fff;
    }

    .high-contrast .message {
        border: 2px solid currentColor;
    }

    /* Focusable messages */
    .message[tabindex] {
        cursor: pointer;
    }

    .message[tabindex]:focus {
        outline: 2px solid var(--color-accent);
        outline-offset: 2px;
    }
`;
document.head.appendChild(style);

export default AccessibilityManager;
