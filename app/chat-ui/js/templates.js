/**
 * Progenitor Chat UI - Prompt Templates Module
 *
 * Provides editing for system prompt files under data/prompts/.
 */

export class PromptTemplates {
    constructor(chatManager) {
        this.chatManager = chatManager;
        this.prompts = [];
        this.currentPrompt = null;
        this.editorModal = null;

        this.init();
    }

    async init() {
        this.createUI();
        this.bindEvents();
        await this.loadPrompts();
    }

    /**
     * Load prompts from server.
     */
    async loadPrompts() {
        try {
            const baseUrl = (window.Config && window.Config.api.baseUrl) || 'http://localhost:8000';
            const response = await fetch(`${baseUrl}/api/ui/prompts`);
            if (!response.ok) throw new Error('Failed to load prompts');

            const data = await response.json();
            this.prompts = data.prompts || [];
            this.renderPromptList();
        } catch (error) {
            console.error('Failed to load prompts:', error);
            this.prompts = [];
        }
    }

    /**
     * Create the templates UI button and dropdown.
     */
    createUI() {
        const inputWrapper = document.querySelector('.input-wrapper');
        if (!inputWrapper) return;

        const templatesBtn = document.createElement('button');
        templatesBtn.id = 'templates-btn';
        templatesBtn.className = 'btn-templates';
        templatesBtn.title = 'System Prompts';
        templatesBtn.innerHTML = `
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                <polyline points="14 2 14 8 20 8"></polyline>
                <line x1="16" y1="13" x2="8" y2="13"></line>
                <line x1="16" y1="17" x2="8" y2="17"></line>
            </svg>
        `;
        inputWrapper.insertBefore(templatesBtn, inputWrapper.firstChild);

        const dropdown = document.createElement('div');
        dropdown.id = 'templates-dropdown';
        dropdown.className = 'templates-dropdown hidden';
        dropdown.innerHTML = `
            <div class="templates-header">
                <h4>System Prompts</h4>
                <button class="templates-close">&times;</button>
            </div>
            <div class="templates-list"></div>
            <div class="templates-footer">
                <button class="btn-reload-prompts">Reload</button>
            </div>
        `;
        inputWrapper.appendChild(dropdown);
        this.dropdown = dropdown;

        this.createEditorModal();
    }

    createEditorModal() {
        const modal = document.createElement('div');
        modal.id = 'prompt-editor-modal';
        modal.className = 'modal hidden';
        modal.innerHTML = `
            <div class="modal-backdrop"></div>
            <div class="modal-content prompt-editor-content">
                <div class="modal-header">
                    <h3 id="prompt-editor-title">Edit Prompt</h3>
                    <button class="modal-close" id="prompt-editor-close">x</button>
                </div>
                <div class="modal-body prompt-editor-body">
                    <div class="prompt-editor-toolbar">
                        <button type="button" class="toolbar-btn" data-action="bold" title="Bold"><strong>B</strong></button>
                        <button type="button" class="toolbar-btn" data-action="italic" title="Italic"><em>I</em></button>
                        <button type="button" class="toolbar-btn" data-action="code" title="Code">&lt;/&gt;</button>
                        <button type="button" class="toolbar-btn" data-action="codeblock" title="Code Block">\`\`\`</button>
                        <span class="toolbar-separator"></span>
                        <button type="button" class="toolbar-btn" data-action="h1" title="H1">H1</button>
                        <button type="button" class="toolbar-btn" data-action="h2" title="H2">H2</button>
                        <button type="button" class="toolbar-btn" data-action="h3" title="H3">H3</button>
                        <span class="toolbar-separator"></span>
                        <button type="button" class="toolbar-btn" data-action="bullet" title="Bullet">-</button>
                        <button type="button" class="toolbar-btn" data-action="number" title="Number">1.</button>
                    </div>
                    <textarea id="prompt-editor-textarea" class="prompt-editor-textarea" spellcheck="false"></textarea>
                    <div class="prompt-editor-preview hidden" id="prompt-editor-preview"></div>
                </div>
                <div class="modal-actions prompt-editor-actions">
                    <button type="button" class="btn btn-secondary" id="prompt-editor-preview-toggle">Preview</button>
                    <div class="prompt-editor-right-actions">
                        <button type="button" class="btn btn-secondary" id="prompt-editor-cancel">Cancel</button>
                        <button type="button" class="btn btn-primary" id="prompt-editor-save">Save</button>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
        this.editorModal = modal;
        this.bindEditorEvents();
    }

    renderPromptList() {
        const listEl = this.dropdown?.querySelector('.templates-list');
        if (!listEl) return;

        if (this.prompts.length === 0) {
            listEl.innerHTML = '<div class="empty-state">No prompt files found</div>';
            return;
        }

        listEl.innerHTML = this.prompts.map(prompt => `
            <div class="template-item" data-filename="${prompt.filename}">
                <span class="template-icon">📄</span>
                <div class="template-info">
                    <span class="template-name">${prompt.name}</span>
                    ${prompt.description ? `<span class="template-preview">${prompt.description}</span>` : ''}
                </div>
                <span class="template-edit-icon">✏️</span>
            </div>
        `).join('');
    }

    bindEvents() {
        const btn = document.getElementById('templates-btn');
        const dropdown = this.dropdown;

        btn?.addEventListener('click', (e) => {
            e.stopPropagation();
            dropdown?.classList.toggle('hidden');
        });

        dropdown?.querySelector('.templates-close')?.addEventListener('click', () => {
            dropdown.classList.add('hidden');
        });

        dropdown?.querySelector('.btn-reload-prompts')?.addEventListener('click', async () => {
            await this.loadPrompts();
            window.notifications?.show('info', 'Reloaded', 'Prompt files reloaded');
        });

        dropdown?.querySelector('.templates-list')?.addEventListener('click', async (e) => {
            const item = e.target.closest('.template-item');
            if (!item) return;
            const filename = item.dataset.filename;
            dropdown.classList.add('hidden');
            await this.openEditor(filename);
        });

        document.addEventListener('click', (e) => {
            if (!dropdown?.contains(e.target) && !btn?.contains(e.target)) {
                dropdown?.classList.add('hidden');
            }
        });
    }

    bindEditorEvents() {
        const modal = this.editorModal;
        if (!modal) return;

        const textarea = modal.querySelector('#prompt-editor-textarea');
        const preview = modal.querySelector('#prompt-editor-preview');
        const backdrop = modal.querySelector('.modal-backdrop');
        const closeBtn = modal.querySelector('#prompt-editor-close');
        const cancelBtn = modal.querySelector('#prompt-editor-cancel');
        const saveBtn = modal.querySelector('#prompt-editor-save');
        const previewToggle = modal.querySelector('#prompt-editor-preview-toggle');
        const toolbar = modal.querySelector('.prompt-editor-toolbar');

        const closeEditor = () => {
            modal.classList.add('hidden');
            modal.classList.remove('visible');
            this.currentPrompt = null;
        };

        closeBtn?.addEventListener('click', closeEditor);
        cancelBtn?.addEventListener('click', closeEditor);
        backdrop?.addEventListener('click', closeEditor);

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && modal.classList.contains('visible')) {
                closeEditor();
            }
        });

        previewToggle?.addEventListener('click', () => {
            const isPreview = preview.classList.contains('hidden');
            if (isPreview) {
                preview.innerHTML = this.renderMarkdown(textarea.value);
                preview.classList.remove('hidden');
                textarea.classList.add('hidden');
                previewToggle.textContent = 'Edit';
            } else {
                preview.classList.add('hidden');
                textarea.classList.remove('hidden');
                previewToggle.textContent = 'Preview';
            }
        });

        saveBtn?.addEventListener('click', async () => {
            if (!this.currentPrompt) return;
            const content = textarea.value;
            try {
                const baseUrl = (window.Config && window.Config.api.baseUrl) || 'http://localhost:8000';
                const response = await fetch(`${baseUrl}/api/ui/prompts/${this.currentPrompt.filename}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content })
                });
                if (!response.ok) throw new Error('Failed to save');
                window.notifications?.show('success', 'Saved', `Prompt saved successfully`);
                closeEditor();
                await this.loadPrompts();
            } catch (error) {
                console.error('Failed to save prompt:', error);
                window.notifications?.show('error', 'Error', 'Failed to save prompt file');
            }
        });

        toolbar?.addEventListener('click', (e) => {
            const btn = e.target.closest('.toolbar-btn');
            if (!btn) return;
            this.applyMarkdownAction(textarea, btn.dataset.action);
        });

        textarea?.addEventListener('keydown', (e) => {
            if (e.ctrlKey || e.metaKey) {
                if (e.key === 'b') { e.preventDefault(); this.applyMarkdownAction(textarea, 'bold'); }
                if (e.key === 'i') { e.preventDefault(); this.applyMarkdownAction(textarea, 'italic'); }
                if (e.key === 's') { e.preventDefault(); saveBtn?.click(); }
            }
            if (e.key === 'Tab') {
                e.preventDefault();
                const start = textarea.selectionStart;
                textarea.value = textarea.value.substring(0, start) + '    ' + textarea.value.substring(textarea.selectionEnd);
                textarea.selectionStart = textarea.selectionEnd = start + 4;
            }
        });
    }

    async openEditor(filename) {
        try {
            const baseUrl = (window.Config && window.Config.api.baseUrl) || 'http://localhost:8000';
            const response = await fetch(`${baseUrl}/api/ui/prompts/${filename}`);
            if (!response.ok) throw new Error('Failed to load prompt');

            const data = await response.json();
            this.currentPrompt = data;

            const modal = this.editorModal;
            const textarea = modal.querySelector('#prompt-editor-textarea');
            const preview = modal.querySelector('#prompt-editor-preview');
            const title = modal.querySelector('#prompt-editor-title');
            const previewToggle = modal.querySelector('#prompt-editor-preview-toggle');

            title.textContent = `Edit: ${data.name}`;
            textarea.value = data.content;
            textarea.classList.remove('hidden');
            preview.classList.add('hidden');
            previewToggle.textContent = 'Preview';

            modal.classList.remove('hidden');
            modal.classList.add('visible');
            textarea.focus();
        } catch (error) {
            console.error('Failed to open prompt editor:', error);
            window.notifications?.show('error', 'Error', 'Failed to load prompt file');
        }
    }

    applyMarkdownAction(textarea, action) {
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        const selected = textarea.value.substring(start, end);
        let replacement = '';

        switch (action) {
            case 'bold': replacement = `**${selected || 'text'}**`; break;
            case 'italic': replacement = `*${selected || 'text'}*`; break;
            case 'code': replacement = `\`${selected || 'code'}\``; break;
            case 'codeblock': replacement = `\n\`\`\`\n${selected || 'code'}\n\`\`\`\n`; break;
            case 'h1': replacement = `# ${selected || 'Heading'}`; break;
            case 'h2': replacement = `## ${selected || 'Heading'}`; break;
            case 'h3': replacement = `### ${selected || 'Heading'}`; break;
            case 'bullet': replacement = `- ${selected || 'item'}`; break;
            case 'number': replacement = `1. ${selected || 'item'}`; break;
        }

        textarea.value = textarea.value.substring(0, start) + replacement + textarea.value.substring(end);
        textarea.focus();
    }

    renderMarkdown(text) {
        // Escape HTML first
        let html = text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');

        // Code blocks (triple backticks) - use RegExp constructor to avoid template literal issues
        html = html.replace(new RegExp('```(\\w*)\\n([\\s\\S]*?)```', 'g'), '<pre><code>$2</code></pre>');

        // Inline code
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

        // Headers
        html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
        html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
        html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');

        // Bold and italic
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

        // Lists
        html = html.replace(/^- (.+)$/gm, '<li>$1</li>');

        // Line breaks
        html = html.replace(/\n\n/g, '<br><br>');
        html = html.replace(/\n/g, '<br>');

        return html;
    }
}

export default PromptTemplates;
