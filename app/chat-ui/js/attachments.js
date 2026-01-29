/**
 * Progenitor Chat UI - File Attachments Module
 * 
 * Provides file upload and attachment functionality.
 */

export class FileAttachments {
    constructor(chatManager) {
        this.chatManager = chatManager;
        this.attachments = [];
        this.maxFileSize = 10 * 1024 * 1024; // 10MB
        this.allowedTypes = [
            'text/plain',
            'text/markdown',
            'text/csv',
            'application/json',
            'application/javascript',
            'text/javascript',
            'text/css',
            'text/html',
            'text/xml',
            'application/xml',
            'text/yaml',
            'application/x-yaml',
            'image/png',
            'image/jpeg',
            'image/gif',
            'image/webp',
            'application/pdf'
        ];
        
        this.init();
    }

    init() {
        this.createUI();
        this.bindEvents();
    }

    /**
     * Create file attachment UI.
     */
    createUI() {
        const inputWrapper = document.querySelector('.input-wrapper');
        if (!inputWrapper) return;

        // Create attach button
        const attachBtn = document.createElement('button');
        attachBtn.id = 'attach-btn';
        attachBtn.className = 'btn-attach';
        attachBtn.title = 'Attach File';
        attachBtn.innerHTML = `
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path>
            </svg>
        `;

        // Insert before templates button or at start
        const templatesBtn = inputWrapper.querySelector('#templates-btn');
        if (templatesBtn) {
            inputWrapper.insertBefore(attachBtn, templatesBtn);
        } else {
            inputWrapper.insertBefore(attachBtn, inputWrapper.firstChild);
        }

        // Create hidden file input
        const fileInput = document.createElement('input');
        fileInput.type = 'file';
        fileInput.id = 'file-input';
        fileInput.multiple = true;
        fileInput.accept = this.allowedTypes.join(',');
        fileInput.style.display = 'none';
        inputWrapper.appendChild(fileInput);

        // Create attachments preview area
        const previewArea = document.createElement('div');
        previewArea.id = 'attachments-preview';
        previewArea.className = 'attachments-preview hidden';
        
        // Insert before textarea
        const textarea = inputWrapper.querySelector('textarea');
        if (textarea) {
            inputWrapper.insertBefore(previewArea, textarea);
        }

        this.attachBtn = attachBtn;
        this.fileInput = fileInput;
        this.previewArea = previewArea;
    }

    /**
     * Bind event handlers.
     */
    bindEvents() {
        // Attach button click
        this.attachBtn?.addEventListener('click', () => {
            this.fileInput?.click();
        });

        // File selection
        this.fileInput?.addEventListener('change', (e) => {
            const files = Array.from(e.target.files || []);
            this.handleFiles(files);
            e.target.value = ''; // Reset for next selection
        });

        // Drag and drop
        const inputArea = document.querySelector('.chat-input');
        if (inputArea) {
            inputArea.addEventListener('dragover', (e) => {
                e.preventDefault();
                inputArea.classList.add('drag-over');
            });

            inputArea.addEventListener('dragleave', () => {
                inputArea.classList.remove('drag-over');
            });

            inputArea.addEventListener('drop', (e) => {
                e.preventDefault();
                inputArea.classList.remove('drag-over');
                const files = Array.from(e.dataTransfer?.files || []);
                this.handleFiles(files);
            });
        }

        // Paste handler for images
        document.addEventListener('paste', (e) => {
            const items = Array.from(e.clipboardData?.items || []);
            const imageItems = items.filter(item => item.type.startsWith('image/'));
            
            if (imageItems.length > 0) {
                e.preventDefault();
                const files = imageItems.map(item => item.getAsFile()).filter(Boolean);
                this.handleFiles(files);
            }
        });

        // Keyboard shortcut: Ctrl+U for upload
        document.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.key === 'u') {
                e.preventDefault();
                this.fileInput?.click();
            }
        });
    }

    /**
     * Handle selected files.
     * @param {File[]} files - Files to handle
     */
    async handleFiles(files) {
        for (const file of files) {
            // Validate file
            const error = this.validateFile(file);
            if (error) {
                window.notifications?.show('error', 'Invalid File', error);
                continue;
            }

            // Process file
            try {
                const attachment = await this.processFile(file);
                this.attachments.push(attachment);
                this.renderPreview();
            } catch (e) {
                window.notifications?.show('error', 'Upload Failed', `Failed to process ${file.name}`);
            }
        }
    }

    /**
     * Validate a file.
     * @param {File} file - File to validate
     * @returns {string|null} Error message or null if valid
     */
    validateFile(file) {
        if (file.size > this.maxFileSize) {
            return `File too large (max ${this.maxFileSize / 1024 / 1024}MB)`;
        }

        if (!this.isAllowedType(file)) {
            return `File type not supported: ${file.type || 'unknown'}`;
        }

        return null;
    }

    /**
     * Check if file type is allowed.
     */
    isAllowedType(file) {
        // Check MIME type
        if (this.allowedTypes.includes(file.type)) {
            return true;
        }

        // Check extension for text files
        const ext = file.name.split('.').pop()?.toLowerCase();
        const textExtensions = ['txt', 'md', 'json', 'js', 'ts', 'py', 'css', 'html', 'xml', 'yaml', 'yml', 'csv', 'log'];
        if (textExtensions.includes(ext)) {
            return true;
        }

        return false;
    }

    /**
     * Process a file.
     * @param {File} file - File to process
     * @returns {Object} Attachment object
     */
    async processFile(file) {
        const attachment = {
            id: Date.now().toString() + Math.random().toString(36).substr(2, 9),
            name: file.name,
            type: file.type,
            size: file.size,
            content: null,
            preview: null
        };

        // Read file content
        if (file.type.startsWith('image/')) {
            // Read as data URL for images
            attachment.content = await this.readAsDataURL(file);
            attachment.preview = attachment.content;
        } else {
            // Read as text for other files
            attachment.content = await this.readAsText(file);
            attachment.preview = this.truncate(attachment.content, 200);
        }

        return attachment;
    }

    /**
     * Read file as text.
     */
    readAsText(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(reader.error);
            reader.readAsText(file);
        });
    }

    /**
     * Read file as data URL.
     */
    readAsDataURL(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(reader.error);
            reader.readAsDataURL(file);
        });
    }

    /**
     * Render attachments preview.
     */
    renderPreview() {
        if (!this.previewArea) return;

        if (this.attachments.length === 0) {
            this.previewArea.classList.add('hidden');
            this.previewArea.innerHTML = '';
            return;
        }

        this.previewArea.classList.remove('hidden');
        this.previewArea.innerHTML = this.attachments.map(att => `
            <div class="attachment-item" data-id="${att.id}">
                ${att.type.startsWith('image/') 
                    ? `<img src="${att.preview}" alt="${att.name}" class="attachment-image">`
                    : `<div class="attachment-icon">${this.getFileIcon(att)}</div>`
                }
                <div class="attachment-info">
                    <span class="attachment-name">${this.escapeHtml(att.name)}</span>
                    <span class="attachment-size">${this.formatSize(att.size)}</span>
                </div>
                <button class="attachment-remove" data-id="${att.id}">&times;</button>
            </div>
        `).join('');

        // Bind remove handlers
        this.previewArea.querySelectorAll('.attachment-remove').forEach(btn => {
            btn.addEventListener('click', () => {
                this.removeAttachment(btn.dataset.id);
            });
        });
    }

    /**
     * Remove an attachment.
     */
    removeAttachment(id) {
        this.attachments = this.attachments.filter(a => a.id !== id);
        this.renderPreview();
    }

    /**
     * Clear all attachments.
     */
    clearAttachments() {
        this.attachments = [];
        this.renderPreview();
    }

    /**
     * Get attachments for sending.
     */
    getAttachments() {
        return this.attachments.map(att => ({
            name: att.name,
            type: att.type,
            content: att.content
        }));
    }

    /**
     * Get file icon based on type.
     */
    getFileIcon(attachment) {
        const ext = attachment.name.split('.').pop()?.toLowerCase();
        const icons = {
            pdf: '📄',
            doc: '📝', docx: '📝',
            txt: '📃', md: '📃',
            json: '📋', yaml: '📋', yml: '📋',
            js: '🟨', ts: '🔷',
            py: '🐍',
            css: '🎨',
            html: '🌐',
            csv: '📊',
            default: '📎'
        };
        return icons[ext] || icons.default;
    }

    /**
     * Format file size.
     */
    formatSize(bytes) {
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    }

    /**
     * Truncate text.
     */
    truncate(text, maxLength) {
        if (text.length <= maxLength) return text;
        return text.substring(0, maxLength) + '...';
    }

    /**
     * Escape HTML.
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Add CSS for file attachments
const style = document.createElement('style');
style.textContent = `
    /* Attach Button */
    .btn-attach {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 36px;
        height: 36px;
        padding: 0;
        background-color: transparent;
        border: 1px solid var(--color-border);
        border-radius: var(--border-radius-md);
        color: var(--color-text-secondary);
        cursor: pointer;
        transition: all var(--transition-fast);
    }

    .btn-attach:hover {
        background-color: var(--color-bg-hover);
        color: var(--color-text-primary);
        border-color: var(--color-accent);
    }

    /* Drag over state */
    .chat-input.drag-over {
        background-color: rgba(88, 166, 255, 0.1);
        border-color: var(--color-accent);
    }

    /* Attachments Preview */
    .attachments-preview {
        display: flex;
        flex-wrap: wrap;
        gap: var(--spacing-sm);
        padding: var(--spacing-sm);
        margin-bottom: var(--spacing-sm);
        background-color: var(--color-bg-tertiary);
        border-radius: var(--border-radius-md);
    }

    .attachments-preview.hidden {
        display: none;
    }

    .attachment-item {
        display: flex;
        align-items: center;
        gap: var(--spacing-sm);
        padding: var(--spacing-xs) var(--spacing-sm);
        background-color: var(--color-bg-secondary);
        border: 1px solid var(--color-border);
        border-radius: var(--border-radius-sm);
        max-width: 200px;
    }

    .attachment-image {
        width: 40px;
        height: 40px;
        object-fit: cover;
        border-radius: var(--border-radius-sm);
    }

    .attachment-icon {
        font-size: 24px;
        width: 40px;
        height: 40px;
        display: flex;
        align-items: center;
        justify-content: center;
    }

    .attachment-info {
        flex: 1;
        min-width: 0;
        display: flex;
        flex-direction: column;
    }

    .attachment-name {
        font-size: var(--font-size-sm);
        color: var(--color-text-primary);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .attachment-size {
        font-size: var(--font-size-xs);
        color: var(--color-text-muted);
    }

    .attachment-remove {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 20px;
        height: 20px;
        padding: 0;
        background: none;
        border: none;
        color: var(--color-text-muted);
        cursor: pointer;
        font-size: 16px;
        line-height: 1;
        border-radius: 50%;
        transition: all var(--transition-fast);
    }

    .attachment-remove:hover {
        background-color: var(--color-error);
        color: white;
    }
`;
document.head.appendChild(style);

export default FileAttachments;
