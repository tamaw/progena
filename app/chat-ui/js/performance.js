/**
 * Progenitor Chat UI - Performance Module
 * 
 * Provides performance optimizations and monitoring.
 */

export class PerformanceManager {
    constructor() {
        this.observer = null;
        this.measurements = [];
        this.lazyLoadObserver = null;
        
        this.init();
    }

    init() {
        this.setupPerformanceObserver();
        this.setupLazyLoading();
        this.setupVirtualScrolling();
        this.optimizeDOMOperations();
        this.setupMemoryManagement();
    }

    /**
     * Setup performance observer for metrics.
     */
    setupPerformanceObserver() {
        if (!window.PerformanceObserver) return;

        try {
            // Long task observer
            const longTaskObserver = new PerformanceObserver((list) => {
                for (const entry of list.getEntries()) {
                    if (entry.duration > 50) {
                        console.warn(`Long task detected: ${entry.duration.toFixed(2)}ms`);
                        window.logs?.warn('Long task detected', { duration: entry.duration });
                    }
                }
            });
            longTaskObserver.observe({ entryTypes: ['longtask'] });

            // Largest Contentful Paint
            const lcpObserver = new PerformanceObserver((list) => {
                const entries = list.getEntries();
                const lastEntry = entries[entries.length - 1];
                this.measurements.push({
                    type: 'LCP',
                    value: lastEntry.startTime,
                    timestamp: Date.now()
                });
            });
            lcpObserver.observe({ entryTypes: ['largest-contentful-paint'] });

            // First Input Delay
            const fidObserver = new PerformanceObserver((list) => {
                const entries = list.getEntries();
                const firstEntry = entries[0];
                this.measurements.push({
                    type: 'FID',
                    value: firstEntry.processingStart - firstEntry.startTime,
                    timestamp: Date.now()
                });
            });
            fidObserver.observe({ entryTypes: ['first-input'] });
        } catch (e) {
            // Observer not supported for this entry type
        }
    }

    /**
     * Setup lazy loading for images and heavy content.
     */
    setupLazyLoading() {
        if (!window.IntersectionObserver) return;

        this.lazyLoadObserver = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const el = entry.target;
                    
                    // Load lazy images
                    if (el.dataset.src) {
                        el.src = el.dataset.src;
                        el.removeAttribute('data-src');
                    }

                    // Load lazy iframes
                    if (el.dataset.lazySrc && el.tagName === 'IFRAME') {
                        el.src = el.dataset.lazySrc;
                        el.removeAttribute('data-lazy-src');
                    }

                    this.lazyLoadObserver.unobserve(el);
                }
            });
        }, {
            rootMargin: '100px',
            threshold: 0.1
        });

        // Observe existing lazy elements
        document.querySelectorAll('[data-src], [data-lazy-src]').forEach(el => {
            this.lazyLoadObserver.observe(el);
        });
    }

    /**
     * Setup virtual scrolling for large message lists.
     */
    setupVirtualScrolling() {
        const messagesContainer = document.querySelector('.chat-messages');
        if (!messagesContainer) return;

        let virtualScrollEnabled = false;
        const THRESHOLD = 100; // Enable when more than 100 messages

        // Create observer to monitor message count
        const containerObserver = new MutationObserver(() => {
            const messageCount = messagesContainer.querySelectorAll('.message').length;
            
            if (messageCount > THRESHOLD && !virtualScrollEnabled) {
                this.enableVirtualScrolling(messagesContainer);
                virtualScrollEnabled = true;
            }
        });

        containerObserver.observe(messagesContainer, { childList: true });
    }

    /**
     * Enable virtual scrolling for a container.
     */
    enableVirtualScrolling(container) {
        // Simple implementation: hide messages far from viewport
        const updateVisibility = this.debounce(() => {
            const containerRect = container.getBoundingClientRect();
            const messages = container.querySelectorAll('.message-wrapper');
            
            messages.forEach(msg => {
                const rect = msg.getBoundingClientRect();
                const isVisible = rect.bottom > containerRect.top - 500 &&
                                 rect.top < containerRect.bottom + 500;
                
                if (isVisible) {
                    msg.style.contentVisibility = 'visible';
                } else {
                    msg.style.contentVisibility = 'hidden';
                    msg.style.containIntrinsicSize = `0 ${rect.height}px`;
                }
            });
        }, 100);

        container.addEventListener('scroll', updateVisibility, { passive: true });
        window.logs?.info('Virtual scrolling enabled for messages');
    }

    /**
     * Optimize DOM operations.
     */
    optimizeDOMOperations() {
        // Batch DOM reads and writes
        this.scheduledReads = [];
        this.scheduledWrites = [];
        this.frameScheduled = false;

        // Override innerHTML to batch writes
        const originalScrollTo = window.scrollTo;
        window.scrollTo = (...args) => {
            requestAnimationFrame(() => originalScrollTo.apply(window, args));
        };
    }

    /**
     * Schedule a DOM read.
     */
    scheduleRead(fn) {
        this.scheduledReads.push(fn);
        this.scheduleFrame();
        return this;
    }

    /**
     * Schedule a DOM write.
     */
    scheduleWrite(fn) {
        this.scheduledWrites.push(fn);
        this.scheduleFrame();
        return this;
    }

    /**
     * Schedule animation frame for batched operations.
     */
    scheduleFrame() {
        if (this.frameScheduled) return;
        this.frameScheduled = true;

        requestAnimationFrame(() => {
            // Run all reads first
            const reads = this.scheduledReads.splice(0);
            reads.forEach(fn => fn());

            // Then run all writes
            const writes = this.scheduledWrites.splice(0);
            writes.forEach(fn => fn());

            this.frameScheduled = false;
        });
    }

    /**
     * Setup memory management.
     */
    setupMemoryManagement() {
        // Clear old messages from DOM when count exceeds threshold
        const MAX_DOM_MESSAGES = 500;
        
        setInterval(() => {
            const container = document.querySelector('.chat-messages');
            if (!container) return;

            const messages = container.querySelectorAll('.message-wrapper');
            if (messages.length > MAX_DOM_MESSAGES) {
                const toRemove = messages.length - MAX_DOM_MESSAGES;
                for (let i = 0; i < toRemove; i++) {
                    messages[i].remove();
                }
                window.logs?.info(`Cleared ${toRemove} old messages from DOM`);
            }
        }, 60000); // Check every minute

        // Listen for memory pressure
        if (navigator.deviceMemory) {
            const lowMemory = navigator.deviceMemory < 4;
            if (lowMemory) {
                document.documentElement.classList.add('low-memory');
                window.logs?.warn('Low memory device detected');
            }
        }
    }

    /**
     * Debounce function.
     */
    debounce(fn, delay) {
        let timeoutId;
        return (...args) => {
            clearTimeout(timeoutId);
            timeoutId = setTimeout(() => fn.apply(this, args), delay);
        };
    }

    /**
     * Throttle function.
     */
    throttle(fn, limit) {
        let inThrottle;
        return (...args) => {
            if (!inThrottle) {
                fn.apply(this, args);
                inThrottle = true;
                setTimeout(() => inThrottle = false, limit);
            }
        };
    }

    /**
     * Measure execution time.
     */
    measure(name, fn) {
        const start = performance.now();
        const result = fn();
        const duration = performance.now() - start;
        
        this.measurements.push({
            type: 'custom',
            name,
            value: duration,
            timestamp: Date.now()
        });

        if (duration > 16) {
            window.logs?.warn(`Slow operation: ${name}`, { duration: `${duration.toFixed(2)}ms` });
        }

        return result;
    }

    /**
     * Get performance report.
     */
    getReport() {
        const report = {
            measurements: this.measurements.slice(-50),
            memory: null,
            timing: null
        };

        // Memory info (if available)
        if (performance.memory) {
            report.memory = {
                usedJSHeapSize: Math.round(performance.memory.usedJSHeapSize / 1024 / 1024),
                totalJSHeapSize: Math.round(performance.memory.totalJSHeapSize / 1024 / 1024)
            };
        }

        // Navigation timing
        if (performance.timing) {
            const t = performance.timing;
            report.timing = {
                pageLoad: t.loadEventEnd - t.navigationStart,
                domContentLoaded: t.domContentLoadedEventEnd - t.navigationStart,
                firstByte: t.responseStart - t.navigationStart
            };
        }

        return report;
    }

    /**
     * Preload resources.
     */
    preload(url, as = 'fetch') {
        const link = document.createElement('link');
        link.rel = 'preload';
        link.href = url;
        link.as = as;
        document.head.appendChild(link);
    }

    /**
     * Prefetch URL for navigation.
     */
    prefetch(url) {
        const link = document.createElement('link');
        link.rel = 'prefetch';
        link.href = url;
        document.head.appendChild(link);
    }
}

// Add CSS for performance optimizations
const style = document.createElement('style');
style.textContent = `
    /* Content visibility for better rendering performance */
    .message-wrapper {
        content-visibility: auto;
        contain-intrinsic-size: 0 100px;
    }

    /* Low memory optimizations */
    .low-memory .message-content img {
        content-visibility: auto;
    }

    .low-memory .code-block {
        max-height: 300px;
        overflow: auto;
    }

    /* Reduce animations for performance */
    @media (prefers-reduced-motion: reduce) {
        * {
            animation-duration: 0.01ms !important;
            transition-duration: 0.01ms !important;
        }
    }

    /* Will-change hints for animated elements */
    .modal,
    .notification,
    .dropdown {
        will-change: transform, opacity;
    }
`;
document.head.appendChild(style);

export default PerformanceManager;
