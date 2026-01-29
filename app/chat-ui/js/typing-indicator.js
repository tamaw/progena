/**
 * Progenitor Chat UI - Typing Indicator Module
 *
 * Shows typing indicators when assistant is responding.
 */

export class TypingIndicator {
    constructor() {
        this.isTyping = false;
        this.indicator = null;
        this.dots = [];
        this.animationFrame = null;

        this.init();
    }

    init() {
        this.createIndicator();
        this.bindEvents();
    }

    /**
     * Create the typing indicator element.
     */
    createIndicator() {
        const indicator = document.createElement('div');
        indicator.id = 'typing-indicator';
        indicator.className = 'typing-indicator hidden';
        indicator.innerHTML = `
            <div class="typing-content">
                <div class="typing-avatar">🤖</div>
                <div class="typing-bubble">
                    <div class="typing-dots">
                        <span class="typing-dot"></span>
                        <span class="typing-dot"></span>
                        <span class="typing-dot"></span>
                    </div>
                    <span class="typing-text">Thinking...</span>
                </div>
            </div>
        `;

        // Insert before input area
        const messagesContainer = document.querySelector('.chat-messages');
        if (messagesContainer) {
            messagesContainer.appendChild(indicator);
        }

        this.indicator = indicator;
        this.dots = indicator.querySelectorAll('.typing-dot');
    }

    /**
     * Bind events.
     */
    bindEvents() {
        // Listen for typing events
        window.addEventListener('assistant-typing', () => this.show());
        window.addEventListener('assistant-done', () => this.hide());
        window.addEventListener('assistant-error', () => this.hide());
    }

    /**
     * Show the typing indicator.
     * @param {string} text - Optional text to show
     */
    show(text = 'Thinking...') {
        if (this.isTyping) return;

        this.isTyping = true;

        const textEl = this.indicator?.querySelector('.typing-text');
        if (textEl) textEl.textContent = text;

        this.indicator?.classList.remove('hidden');
        this.startAnimation();

        // Scroll to bottom
        const container = document.querySelector('.chat-messages');
        if (container) {
            container.scrollTop = container.scrollHeight;
        }

        // Announce for screen readers
        window.accessibility?.announce('Assistant is typing');
    }

    /**
     * Hide the typing indicator.
     */
    hide() {
        if (!this.isTyping) return;

        this.isTyping = false;
        this.indicator?.classList.add('hidden');
        this.stopAnimation();
    }

    /**
     * Start dot animation.
     */
    startAnimation() {
        let step = 0;

        const animate = () => {
            if (!this.isTyping) return;

            this.dots.forEach((dot, i) => {
                const delay = i * 0.15;
                const phase = (step * 0.1 + delay) % 1;
                const scale = 1 + Math.sin(phase * Math.PI) * 0.3;
                const opacity = 0.4 + Math.sin(phase * Math.PI) * 0.6;

                dot.style.transform = `scale(${scale})`;
                dot.style.opacity = opacity;
            });

            step++;
            this.animationFrame = requestAnimationFrame(animate);
        };

        animate();
    }

    /**
     * Stop dot animation.
     */
    stopAnimation() {
        if (this.animationFrame) {
            cancelAnimationFrame(this.animationFrame);
            this.animationFrame = null;
        }

        this.dots.forEach(dot => {
            dot.style.transform = '';
            dot.style.opacity = '';
        });
    }

    /**
     * Update typing text (for progress updates).
     * @param {string} text - New text to display
     */
    updateText(text) {
        const textEl = this.indicator?.querySelector('.typing-text');
        if (textEl) textEl.textContent = text;
    }
}

// Add CSS for typing indicator
const style = document.createElement('style');
style.textContent = `
    /* Typing Indicator */
    .typing-indicator {
        padding: var(--spacing-md);
        animation: fadeIn 0.2s ease;
    }

    .typing-indicator.hidden {
        display: none;
    }

    .typing-content {
        display: flex;
        align-items: flex-end;
        gap: var(--spacing-sm);
    }

    .typing-avatar {
        width: 32px;
        height: 32px;
        font-size: 20px;
        display: flex;
        align-items: center;
        justify-content: center;
        background-color: var(--color-bg-tertiary);
        border-radius: 50%;
    }

    .typing-bubble {
        display: flex;
        align-items: center;
        gap: var(--spacing-sm);
        padding: var(--spacing-sm) var(--spacing-md);
        background-color: var(--color-assistant-bg);
        border-radius: var(--border-radius-lg);
        border-bottom-left-radius: var(--border-radius-sm);
    }

    .typing-dots {
        display: flex;
        gap: 4px;
    }

    .typing-dot {
        width: 8px;
        height: 8px;
        background-color: var(--color-text-secondary);
        border-radius: 50%;
        transition: transform 0.1s ease, opacity 0.1s ease;
    }

    .typing-text {
        font-size: var(--font-size-sm);
        color: var(--color-text-secondary);
        margin-left: var(--spacing-xs);
    }

    /* Alternative animated dots using CSS */
    @keyframes typingDot {
        0%, 60%, 100% {
            transform: translateY(0);
            opacity: 0.4;
        }
        30% {
            transform: translateY(-4px);
            opacity: 1;
        }
    }

    .typing-dots.css-animated .typing-dot {
        animation: typingDot 1.4s ease infinite;
    }

    .typing-dots.css-animated .typing-dot:nth-child(1) {
        animation-delay: 0s;
    }

    .typing-dots.css-animated .typing-dot:nth-child(2) {
        animation-delay: 0.2s;
    }

    .typing-dots.css-animated .typing-dot:nth-child(3) {
        animation-delay: 0.4s;
    }
`;
document.head.appendChild(style);

export default TypingIndicator;
