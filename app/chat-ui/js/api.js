/**
 * Progenitor Chat UI - API Client
 *
 * Handles all communication with the Progenitor chat server.
 * Supports both regular requests and Server-Sent Events for streaming.
 * Includes automatic retry logic and connection recovery.
 */

class ProgenitorAPI {
    constructor(config) {
        this.config = config;
        this.abortController = null;
        this.connectionState = 'disconnected';
        this.lastSuccessfulRequest = null;
        this.failureCount = 0;
        this.maxFailures = 5;
        this.backoffMs = 1000;
    }

    /**
     * Update connection state and notify listeners.
     * @param {string} state - New connection state
     */
    setConnectionState(state) {
        const oldState = this.connectionState;
        this.connectionState = state;

        if (oldState !== state) {
            // Dispatch custom event for UI updates
            window.dispatchEvent(new CustomEvent('api-connection-change', {
                detail: { state, previousState: oldState }
            }));
        }
    }

    /**
     * Record a successful request.
     */
    recordSuccess() {
        this.lastSuccessfulRequest = Date.now();
        this.failureCount = 0;
        this.backoffMs = 1000;
        this.setConnectionState('connected');
    }

    /**
     * Record a failed request.
     * @param {Error} error - The error that occurred
     */
    recordFailure(error) {
        this.failureCount++;

        if (this.failureCount >= this.maxFailures) {
            this.setConnectionState('error');
        } else {
            this.setConnectionState('degraded');
        }

        // Exponential backoff
        this.backoffMs = Math.min(this.backoffMs * 2, 30000);
    }

    /**
     * Make a fetch request with error handling, retries, and circuit breaker.
     * @param {string} endpoint - API endpoint
     * @param {Object} options - Fetch options
     * @returns {Promise<Response>}
     */
    async fetch(endpoint, options = {}) {
        const url = this.config.getApiUrl(endpoint);
        const defaultOptions = {
            headers: {
                'Content-Type': 'application/json',
            },
        };

        const mergedOptions = {
            ...defaultOptions,
            ...options,
            headers: {
                ...defaultOptions.headers,
                ...options.headers,
            },
        };

        let lastError;

        // Check if we should skip due to circuit breaker
        if (this.connectionState === 'error') {
            // Allow retry after backoff period
            const timeSinceLastAttempt = Date.now() - (this.lastSuccessfulRequest || 0);
            if (timeSinceLastAttempt < this.backoffMs) {
                throw new Error('Connection temporarily unavailable. Please try again.');
            }
        }

        for (let attempt = 0; attempt < this.config.api.retryAttempts; attempt++) {
            try {
                this.setConnectionState('connecting');

                const response = await fetch(url, mergedOptions);

                if (!response.ok) {
                    const error = await response.json().catch(() => ({
                        error: { message: response.statusText }
                    }));

                    // Handle specific HTTP errors
                    if (response.status === 429) {
                        const retryAfter = response.headers.get('Retry-After') || 60;
                        throw new RateLimitError(`Rate limit exceeded. Try again in ${retryAfter} seconds.`);
                    }

                    if (response.status >= 500) {
                        throw new ServerError(error.error?.message || `Server error: ${response.status}`);
                    }

                    throw new APIError(error.error?.message || `HTTP ${response.status}: ${response.statusText}`);
                }

                this.recordSuccess();
                return response;

            } catch (error) {
                lastError = error;

                // Don't retry on abort or specific error types
                if (error.name === 'AbortError' ||
                    error instanceof RateLimitError ||
                    error instanceof APIError) {
                    throw error;
                }

                this.recordFailure(error);

                // Wait before retrying with exponential backoff
                if (attempt < this.config.api.retryAttempts - 1) {
                    const delay = this.config.api.retryDelay * Math.pow(2, attempt);
                    await this.sleep(delay);
                }
            }
        }

        throw lastError;
    }

    /**
     * Sleep for a given duration.
     * @param {number} ms - Milliseconds to sleep
     * @returns {Promise<void>}
     */
    sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    /**
     * Get health status of the server.
     * @returns {Promise<Object>}
     */
    async getHealth() {
        const response = await this.fetch('health');
        return response.json();
    }

    /**
     * Get detailed health status.
     * @returns {Promise<Object>}
     */
    async getHealthDetailed() {
        const response = await this.fetch('healthDetailed');
        return response.json();
    }

    /**
     * Get available models.
     * @returns {Promise<Object>}
     */
    async getModels() {
        const response = await this.fetch('models');
        return response.json();
    }

    /**
     * Get metrics.
     * @returns {Promise<Object>}
     */
    async getMetrics() {
        const response = await this.fetch('/metrics/json');
        return response.json();
    }

    /**
     * Send a chat message (non-streaming).
     * @param {string} message - User message
     * @param {Array} history - Previous messages
     * @returns {Promise<Object>}
     */
    async sendMessage(message, history = []) {
        const messages = [
            ...history,
            { role: 'user', content: message }
        ];

        const response = await this.fetch('chat', {
            method: 'POST',
            body: JSON.stringify({
                model: this.config.chat.defaultModel,
                messages: messages,
                stream: false
            }),
        });

        return response.json();
    }

    /**
     * Send a chat message with streaming response.
     * @param {string} message - User message
     * @param {Array} history - Previous messages
     * @param {Function} onChunk - Callback for each chunk
     * @param {Function} onComplete - Callback when complete
     * @param {Function} onError - Callback on error
     * @returns {Promise<void>}
     */
    async sendMessageStream(message, history = [], onChunk, onComplete, onError) {
        // Cancel any existing stream
        this.cancelStream();

        this.abortController = new AbortController();

        const messages = [
            ...history,
            { role: 'user', content: message }
        ];

        const url = this.config.getApiUrl('chat');

        try {
            const response = await fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    model: this.config.chat.defaultModel,
                    messages: messages,
                    stream: true
                }),
                signal: this.abortController.signal
            });

            if (!response.ok) {
                const error = await response.json().catch(() => ({ error: { message: response.statusText } }));
                throw new Error(error.error?.message || `HTTP ${response.status}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();

                if (done) {
                    break;
                }

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop(); // Keep incomplete line in buffer

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        const data = line.slice(6).trim();

                        if (data === '[DONE]') {
                            onComplete?.();
                            return;
                        }

                        try {
                            const parsed = JSON.parse(data);

                            if (parsed.error) {
                                onError?.(new Error(parsed.error));
                                return;
                            }

                            // Handle chat completion chunk format
                            if (parsed.choices?.[0]?.delta?.content) {
                                onChunk?.(parsed.choices[0].delta.content);
                            }

                            // Check for finish reason
                            if (parsed.choices?.[0]?.finish_reason === 'stop') {
                                onComplete?.();
                                return;
                            }
                        } catch (e) {
                            // Ignore parse errors for incomplete JSON
                            console.debug('Parse error:', e, data);
                        }
                    }
                }
            }

            onComplete?.();
        } catch (error) {
            if (error.name === 'AbortError') {
                onComplete?.('cancelled');
            } else {
                onError?.(error);
            }
        }
    }

    /**
     * Send a message using the Responses API with streaming.
     * @param {string} message - User message
     * @param {Function} onChunk - Callback for each chunk
     * @param {Function} onComplete - Callback when complete
     * @param {Function} onError - Callback on error
     */
    async sendResponseStream(message, onChunk, onComplete, onError) {
        this.cancelStream();
        this.abortController = new AbortController();

        const url = this.config.getApiUrl('responses');

        try {
            const response = await fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    model: this.config.chat.defaultModel,
                    input: message,
                    stream: true
                }),
                signal: this.abortController.signal
            });

            if (!response.ok) {
                const error = await response.json().catch(() => ({ error: { message: response.statusText } }));
                throw new Error(error.error?.message || `HTTP ${response.status}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();

                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();

                for (const line of lines) {
                    if (line.startsWith('event: ')) {
                        // Handle SSE events
                        continue;
                    }

                    if (line.startsWith('data: ')) {
                        const data = line.slice(6).trim();

                        try {
                            const parsed = JSON.parse(data);

                            if (parsed.delta?.text) {
                                onChunk?.(parsed.delta.text);
                            }

                            if (parsed.object === 'response.completed') {
                                onComplete?.();
                                return;
                            }
                        } catch (e) {
                            console.debug('Parse error:', e);
                        }
                    }
                }
            }

            onComplete?.();
        } catch (error) {
            if (error.name === 'AbortError') {
                onComplete?.('cancelled');
            } else {
                onError?.(error);
            }
        }
    }

    /**
     * Cancel any ongoing stream.
     */
    cancelStream() {
        if (this.abortController) {
            this.abortController.abort();
            this.abortController = null;
        }
    }

    /**
     * Check if currently streaming.
     * @returns {boolean}
     */
    isStreaming() {
        return this.abortController !== null;
    }

    /**
     * Get current connection state.
     * @returns {string}
     */
    getConnectionState() {
        return this.connectionState;
    }

    /**
     * Check if API is healthy.
     * @returns {Promise<boolean>}
     */
    async isHealthy() {
        try {
            await this.getHealth();
            return true;
        } catch {
            return false;
        }
    }

    /**
     * Reset connection state and retry.
     */
    resetConnection() {
        this.failureCount = 0;
        this.backoffMs = 1000;
        this.setConnectionState('disconnected');
    }
}


// Custom Error Classes for better error handling
class APIError extends Error {
    constructor(message) {
        super(message);
        this.name = 'APIError';
    }
}

class RateLimitError extends APIError {
    constructor(message) {
        super(message);
        this.name = 'RateLimitError';
    }
}

class ServerError extends APIError {
    constructor(message) {
        super(message);
        this.name = 'ServerError';
    }
}

class ConnectionError extends APIError {
    constructor(message) {
        super(message);
        this.name = 'ConnectionError';
    }
}


// Export error classes
window.APIError = APIError;
window.RateLimitError = RateLimitError;
window.ServerError = ServerError;
window.ConnectionError = ConnectionError;

// Create and export the API instance
window.api = new ProgenitorAPI(Config);
